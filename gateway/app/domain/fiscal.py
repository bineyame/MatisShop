"""Fiscal rail service.

Owns the sequence that makes fiscalization safe:

    claim idempotency -> create/reuse document -> transition to submitting
    -> call provider with bounded retries -> transition to registered|failed
    -> record every attempt and transition

The two properties the demo has to prove live here and in the provider:
exactly one registration per logical document, and a failed submission that
stays retryable without ever producing a second IRN.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.audit import AuditService
from app.domain.contracts import FiscalDocumentRequest, FiscalDocumentResponse
from app.domain.errors import (
    IdempotencyConflict,
    OperationInProgress,
    ProviderError,
    ProviderPermanentError,
    ProviderTransientError,
    ResourceNotFound,
)
from app.domain.idempotency import IdempotencyService, compute_request_hash
from app.domain.state_machine import assert_transition
from app.observability import get_logger
from app.persistence.models import FiscalDocument
from app.providers.fiscal.base import FiscalProvider

logger = get_logger(__name__)

RAIL = "fiscal"
SCOPE_REGISTER = "fiscal.register"
RESOURCE = "fiscal_document"


class FiscalService:
    def __init__(
        self,
        session: AsyncSession,
        provider: FiscalProvider,
        settings: Settings,
        *,
        correlation_id: str | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.settings = settings
        self.correlation_id = correlation_id
        self.idempotency = IdempotencyService(session)
        self.audit = AuditService(session)

    # -- public API ----------------------------------------------------------
    async def register(self, request: FiscalDocumentRequest) -> FiscalDocumentResponse:
        request_hash = compute_request_hash(request)
        claim = await self.idempotency.claim(
            scope=SCOPE_REGISTER,
            key=request.idempotency_key,
            request_hash=request_hash,
            correlation_id=self.correlation_id,
        )

        if claim.state == "conflict":
            raise IdempotencyConflict(
                "idempotency key already used with a different request body",
                detail={"idempotency_key": request.idempotency_key},
            )
        if claim.state == "replay":
            document = await self._require_document(request.idempotency_key)
            return self._to_response(document, replayed=True)
        if claim.state == "in_progress":
            raise OperationInProgress(
                "a request with this idempotency key is currently being processed",
                detail={"idempotency_key": request.idempotency_key},
            )

        document = await self._get_or_create(request)

        # Defensive: the document is registered but the idempotency record was
        # lost/reset. Never re-submit a registered document.
        if document.status == "registered":
            response = self._to_response(document, replayed=True)
            await self.idempotency.complete(
                claim.record,
                resource_type=RESOURCE,
                resource_id=document.id,
                response=response,
            )
            return response

        return await self._submit(document, request, claim)

    async def retry(self, document_id: str) -> FiscalDocumentResponse:
        """Re-submit a failed document using its stored request payload.

        Same idempotency key, therefore the same logical identity: a retry can
        never produce a second registration.
        """
        document = await self.session.get(FiscalDocument, document_id)
        if document is None:
            raise ResourceNotFound(f"fiscal document {document_id} not found")
        if document.status == "registered":
            return self._to_response(document, replayed=True)
        if document.status == "cancelled":
            raise ProviderPermanentError(
                "cancelled documents cannot be retried", provider=self.provider.name
            )
        if not document.request_payload:  # pragma: no cover - defensive
            raise ResourceNotFound(f"fiscal document {document_id} has no stored request payload")

        request = FiscalDocumentRequest.model_validate(document.request_payload)
        claim = await self.idempotency.claim(
            scope=SCOPE_REGISTER,
            key=request.idempotency_key,
            request_hash=compute_request_hash(request),
            correlation_id=self.correlation_id,
        )
        if claim.state == "replay":
            return self._to_response(document, replayed=True)
        if claim.state == "in_progress":
            raise OperationInProgress("this document is already being submitted")
        return await self._submit(document, request, claim)

    async def get(self, document_id: str) -> FiscalDocumentResponse:
        document = await self.session.get(FiscalDocument, document_id)
        if document is None:
            raise ResourceNotFound(f"fiscal document {document_id} not found")
        return self._to_response(document)

    async def get_by_idempotency_key(self, key: str) -> FiscalDocumentResponse:
        document = await self._require_document(key)
        return self._to_response(document)

    async def cancel(self, document_id: str, reason: str) -> FiscalDocumentResponse:
        document = await self.session.get(FiscalDocument, document_id)
        if document is None:
            raise ResourceNotFound(f"fiscal document {document_id} not found")
        assert_transition(RAIL, document.status, "cancelled")
        reference = document.irn or document.provider_transaction_id or ""
        started = _now_ms()
        try:
            result = await self.provider.cancel(reference, reason)
        except ProviderError as exc:
            await self.audit.record_attempt(
                rail=RAIL,
                operation="cancel",
                provider=self.provider.name,
                outcome="permanent_error" if not getattr(exc, "retryable", False) else "transient_error",
                attempt=1,
                duration_ms=_now_ms() - started,
                resource_type=RESOURCE,
                resource_id=document.id,
                idempotency_key=document.idempotency_key,
                correlation_id=self.correlation_id,
                error=str(exc),
            )
            raise

        previous = document.status
        document.status = "cancelled"
        document.cancelled_at = result.cancelled_at
        await self.audit.record_attempt(
            rail=RAIL,
            operation="cancel",
            provider=self.provider.name,
            outcome="success",
            attempt=1,
            duration_ms=_now_ms() - started,
            resource_type=RESOURCE,
            resource_id=document.id,
            idempotency_key=document.idempotency_key,
            correlation_id=self.correlation_id,
            response_payload=result.raw_response,
        )
        await self.audit.record_event(
            rail=RAIL,
            resource_type=RESOURCE,
            resource_id=document.id,
            event_type="cancelled",
            from_status=previous,
            to_status="cancelled",
            detail={"reason": reason},
            correlation_id=self.correlation_id,
        )
        await self.session.flush()
        return self._to_response(document)

    # -- internals -----------------------------------------------------------
    async def _submit(
        self, document: FiscalDocument, request: FiscalDocumentRequest, claim
    ) -> FiscalDocumentResponse:
        previous = document.status
        assert_transition(RAIL, previous, "submitting")
        document.status = "submitting"
        document.submitted_at = document.submitted_at or _utcnow()
        await self.audit.record_event(
            rail=RAIL,
            resource_type=RESOURCE,
            resource_id=document.id,
            event_type="submitting",
            from_status=previous,
            to_status="submitting",
            correlation_id=self.correlation_id,
        )
        await self.session.flush()

        max_attempts = self.settings.gateway_max_attempts
        base_delay = self.settings.gateway_retry_base_delay_ms / 1000.0
        timeout = self.settings.gateway_provider_timeout_seconds
        request_payload = request.model_dump(mode="json")
        last_error: str | None = None

        for attempt in range(1, max_attempts + 1):
            document.attempt_count += 1
            started = _now_ms()
            try:
                result = await asyncio.wait_for(
                    self.provider.register(request, attempt=document.attempt_count),
                    timeout=timeout,
                )
            except TimeoutError:
                last_error = f"provider timed out after {timeout}s"
                await self._record_failed_attempt(
                    document, request_payload, "transient_error", last_error, started
                )
            except ProviderTransientError as exc:
                last_error = str(exc)
                await self._record_failed_attempt(
                    document, request_payload, "transient_error", last_error, started
                )
            except (ProviderPermanentError, ProviderError) as exc:
                last_error = str(exc)
                await self._record_failed_attempt(
                    document, request_payload, "permanent_error", last_error, started
                )
                break
            else:
                await self.audit.record_attempt(
                    rail=RAIL,
                    operation="register",
                    provider=self.provider.name,
                    outcome="success",
                    attempt=document.attempt_count,
                    duration_ms=_now_ms() - started,
                    resource_type=RESOURCE,
                    resource_id=document.id,
                    idempotency_key=document.idempotency_key,
                    correlation_id=self.correlation_id,
                    request_payload=request_payload,
                    response_payload=result.raw_response,
                )
                return await self._mark_registered(document, result, claim)

            if attempt < max_attempts:
                await asyncio.sleep(base_delay * (2 ** (attempt - 1)))

        return await self._mark_failed(document, last_error or "unknown provider error", claim)

    async def _record_failed_attempt(
        self,
        document: FiscalDocument,
        request_payload: dict[str, Any],
        outcome: str,
        error: str,
        started_ms: int,
    ) -> None:
        await self.audit.record_attempt(
            rail=RAIL,
            operation="register",
            provider=self.provider.name,
            outcome=outcome,
            attempt=document.attempt_count,
            duration_ms=_now_ms() - started_ms,
            resource_type=RESOURCE,
            resource_id=document.id,
            idempotency_key=document.idempotency_key,
            correlation_id=self.correlation_id,
            request_payload=request_payload,
            error=error,
        )

    async def _mark_registered(self, document: FiscalDocument, result, claim) -> FiscalDocumentResponse:
        assert_transition(RAIL, document.status, "registered")
        document.status = "registered"
        document.provider_transaction_id = result.provider_transaction_id
        document.irn = result.irn
        document.qr_payload = result.qr_payload
        document.registered_at = result.registered_at
        document.provider_response = result.raw_response
        document.last_error = None
        await self.audit.record_event(
            rail=RAIL,
            resource_type=RESOURCE,
            resource_id=document.id,
            event_type="registered",
            from_status="submitting",
            to_status="registered",
            detail={"irn": result.irn, "provider_transaction_id": result.provider_transaction_id},
            correlation_id=self.correlation_id,
        )
        response = self._to_response(document)
        await self.idempotency.complete(
            claim.record, resource_type=RESOURCE, resource_id=document.id, response=response
        )
        await self.session.flush()
        logger.info(
            "fiscal.registered",
            extra={
                "resource_id": document.id,
                "idempotency_key": document.idempotency_key,
                "provider": self.provider.name,
                "provider_transaction_id": document.provider_transaction_id,
                "irn": document.irn,
                "attempt_count": document.attempt_count,
                "correlation_id": self.correlation_id,
            },
        )
        return response

    async def _mark_failed(
        self, document: FiscalDocument, error: str, claim
    ) -> FiscalDocumentResponse:
        assert_transition(RAIL, document.status, "failed")
        document.status = "failed"
        document.last_error = error
        await self.audit.record_event(
            rail=RAIL,
            resource_type=RESOURCE,
            resource_id=document.id,
            event_type="failed",
            from_status="submitting",
            to_status="failed",
            detail={"error": error, "attempt_count": document.attempt_count},
            correlation_id=self.correlation_id,
        )
        # Claim is released as `failed`, not `completed`: the same key may be
        # retried later and must reach exactly one registration.
        await self.idempotency.fail(
            claim.record, error=error, resource_type=RESOURCE, resource_id=document.id
        )
        await self.session.flush()
        logger.warning(
            "fiscal.failed",
            extra={
                "resource_id": document.id,
                "idempotency_key": document.idempotency_key,
                "provider": self.provider.name,
                "attempt_count": document.attempt_count,
                "error": error,
                "correlation_id": self.correlation_id,
            },
        )
        return self._to_response(document)

    async def _get_or_create(self, request: FiscalDocumentRequest) -> FiscalDocument:
        existing = await self._find_document(request.idempotency_key)
        if existing is not None:
            return existing

        document = FiscalDocument(
            idempotency_key=request.idempotency_key,
            correlation_id=self.correlation_id,
            source_system=request.source.system,
            source_model=request.source.model,
            source_record_id=request.source.record_id,
            external_reference=request.source.reference,
            document_type=request.document.type,
            document_number=request.document.number,
            issued_at=request.document.issued_at,
            status="pending",
            currency=request.totals.currency,
            subtotal=request.totals.subtotal,
            tax_total=request.totals.tax_total,
            grand_total=request.totals.grand_total,
            provider=self.provider.name,
            request_payload=request.model_dump(mode="json"),
        )
        try:
            # add() inside the savepoint: see the note in IdempotencyService.claim.
            async with self.session.begin_nested():
                self.session.add(document)
                await self.session.flush()
        except IntegrityError:
            existing = await self._find_document(request.idempotency_key)
            if existing is None:  # pragma: no cover - defensive
                raise
            return existing

        await self.audit.record_event(
            rail=RAIL,
            resource_type=RESOURCE,
            resource_id=document.id,
            event_type="created",
            to_status="pending",
            detail={
                "source_model": document.source_model,
                "source_record_id": document.source_record_id,
                "document_number": document.document_number,
            },
            correlation_id=self.correlation_id,
        )
        return document

    async def _find_document(self, idempotency_key: str) -> FiscalDocument | None:
        stmt = select(FiscalDocument).where(FiscalDocument.idempotency_key == idempotency_key)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _require_document(self, idempotency_key: str) -> FiscalDocument:
        document = await self._find_document(idempotency_key)
        if document is None:  # pragma: no cover - defensive
            raise ResourceNotFound(f"no fiscal document for idempotency key {idempotency_key}")
        return document

    def _to_response(self, document: FiscalDocument, *, replayed: bool = False) -> FiscalDocumentResponse:
        return FiscalDocumentResponse(
            id=document.id,
            idempotency_key=document.idempotency_key,
            status=document.status,
            provider=document.provider,
            provider_transaction_id=document.provider_transaction_id,
            irn=document.irn,
            qr_payload=document.qr_payload,
            document_number=document.document_number,
            currency=document.currency,
            grand_total=document.grand_total,
            attempt_count=document.attempt_count,
            last_error=document.last_error,
            registered_at=document.registered_at,
            submitted_at=document.submitted_at,
            correlation_id=document.correlation_id,
            replayed=replayed,
        )


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)
