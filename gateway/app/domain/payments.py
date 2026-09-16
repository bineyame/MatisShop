"""Payment rail service.

Same skeleton as the fiscal rail - claim, persist, call, audit - because the
guarantees are the same: one logical payment is attempted at most once, and a
lost response never charges a customer twice.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.audit import AuditService
from app.domain.contracts import PaymentRequest, PaymentResponse, RefundRequest
from app.domain.errors import (
    IdempotencyConflict,
    OperationInProgress,
    ProviderError,
    ResourceNotFound,
    ValidationError,
)
from app.domain.idempotency import IdempotencyService, compute_request_hash
from app.domain.state_machine import assert_transition, can_transition
from app.observability import get_logger
from app.persistence.models import PaymentTransaction, utcnow
from app.providers.payment.base import PaymentProvider

logger = get_logger(__name__)

RAIL = "payment"
SCOPE_CREATE = "payment.create"
RESOURCE = "payment_transaction"


class PaymentService:
    def __init__(
        self,
        session: AsyncSession,
        provider: PaymentProvider,
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

    async def create(self, request: PaymentRequest) -> PaymentResponse:
        claim = await self.idempotency.claim(
            scope=SCOPE_CREATE,
            key=request.idempotency_key,
            request_hash=compute_request_hash(request),
            correlation_id=self.correlation_id,
        )
        if claim.state == "conflict":
            raise IdempotencyConflict(
                "idempotency key already used with a different request body",
                detail={"idempotency_key": request.idempotency_key},
            )
        if claim.state == "replay":
            transaction = await self._require(request.idempotency_key)
            return self._to_response(transaction, replayed=True)
        if claim.state == "in_progress":
            raise OperationInProgress("this payment is already being processed")

        transaction = await self._get_or_create(request)
        if transaction.status in ("succeeded", "authorized"):
            response = self._to_response(transaction, replayed=True)
            await self.idempotency.complete(
                claim.record, resource_type=RESOURCE, resource_id=transaction.id, response=response
            )
            return response

        transaction.attempt_count += 1
        started = _now_ms()
        try:
            result = await self.provider.create_payment(request)
        except ProviderError as exc:
            await self.audit.record_attempt(
                rail=RAIL,
                operation="create_payment",
                provider=self.provider.name,
                outcome="transient_error" if getattr(exc, "retryable", False) else "permanent_error",
                attempt=transaction.attempt_count,
                duration_ms=_now_ms() - started,
                resource_type=RESOURCE,
                resource_id=transaction.id,
                idempotency_key=transaction.idempotency_key,
                correlation_id=self.correlation_id,
                request_payload=request.model_dump(mode="json"),
                error=str(exc),
            )
            previous = transaction.status
            assert_transition(RAIL, previous, "failed")
            transaction.status = "failed"
            transaction.last_error = str(exc)
            await self.audit.record_event(
                rail=RAIL,
                resource_type=RESOURCE,
                resource_id=transaction.id,
                event_type="failed",
                from_status=previous,
                to_status="failed",
                detail={"error": str(exc)},
                correlation_id=self.correlation_id,
            )
            await self.idempotency.fail(
                claim.record, error=str(exc), resource_type=RESOURCE, resource_id=transaction.id
            )
            await self.session.flush()
            return self._to_response(transaction)

        previous = transaction.status
        if result.status != previous:
            assert_transition(RAIL, previous, result.status)
            transaction.status = result.status
        transaction.provider_transaction_id = result.provider_transaction_id
        transaction.checkout_url = result.checkout_url
        transaction.provider_response = result.raw_response
        transaction.authorized_at = result.authorized_at
        if result.status == "succeeded":
            transaction.settled_at = utcnow()

        await self.audit.record_attempt(
            rail=RAIL,
            operation="create_payment",
            provider=self.provider.name,
            outcome="success",
            attempt=transaction.attempt_count,
            duration_ms=_now_ms() - started,
            resource_type=RESOURCE,
            resource_id=transaction.id,
            idempotency_key=transaction.idempotency_key,
            correlation_id=self.correlation_id,
            request_payload=request.model_dump(mode="json"),
            response_payload=result.raw_response,
        )
        await self.audit.record_event(
            rail=RAIL,
            resource_type=RESOURCE,
            resource_id=transaction.id,
            event_type=result.status,
            from_status=previous,
            to_status=transaction.status,
            correlation_id=self.correlation_id,
        )
        response = self._to_response(transaction)
        await self.idempotency.complete(
            claim.record, resource_type=RESOURCE, resource_id=transaction.id, response=response
        )
        await self.session.flush()
        return response

    async def verify(self, transaction_id: str) -> PaymentResponse:
        """Ask the provider for authoritative state and reconcile locally."""
        transaction = await self._get(transaction_id)
        if not transaction.provider_transaction_id:
            return self._to_response(transaction)

        started = _now_ms()
        result = await self.provider.verify_payment(transaction.provider_transaction_id)
        await self.audit.record_attempt(
            rail=RAIL,
            operation="verify_payment",
            provider=self.provider.name,
            outcome="success",
            attempt=1,
            duration_ms=_now_ms() - started,
            resource_type=RESOURCE,
            resource_id=transaction.id,
            idempotency_key=transaction.idempotency_key,
            correlation_id=self.correlation_id,
            response_payload=result.raw_response,
        )
        await self._apply_status(transaction, result.status)
        return self._to_response(transaction)

    async def refund(self, transaction_id: str, request: RefundRequest) -> PaymentResponse:
        transaction = await self._get(transaction_id)
        if transaction.status not in ("succeeded", "partially_refunded"):
            raise ValidationError(
                f"payment is '{transaction.status}' and cannot be refunded",
                detail={"status": transaction.status},
            )
        amount = request.amount if request.amount is not None else transaction.amount
        remaining = transaction.amount - transaction.refunded_amount
        if amount > remaining:
            raise ValidationError(
                "refund exceeds refundable amount",
                detail={"requested": str(amount), "refundable": str(remaining)},
            )

        claim = await self.idempotency.claim(
            scope="payment.refund",
            key=request.idempotency_key,
            request_hash=compute_request_hash(request),
            correlation_id=self.correlation_id,
        )
        if claim.state == "conflict":
            raise IdempotencyConflict("refund idempotency key reused with a different body")
        if claim.state == "replay":
            return self._to_response(transaction, replayed=True)

        started = _now_ms()
        result = await self.provider.refund(transaction.provider_transaction_id or "", amount)
        transaction.refunded_amount = transaction.refunded_amount + amount
        previous = transaction.status
        target = "refunded" if transaction.refunded_amount >= transaction.amount else "partially_refunded"
        assert_transition(RAIL, previous, target)
        transaction.status = target
        await self.audit.record_attempt(
            rail=RAIL,
            operation="refund",
            provider=self.provider.name,
            outcome="success",
            attempt=1,
            duration_ms=_now_ms() - started,
            resource_type=RESOURCE,
            resource_id=transaction.id,
            idempotency_key=request.idempotency_key,
            correlation_id=self.correlation_id,
            response_payload=result.raw_response,
        )
        await self.audit.record_event(
            rail=RAIL,
            resource_type=RESOURCE,
            resource_id=transaction.id,
            event_type="refund",
            from_status=previous,
            to_status=target,
            detail={"amount": str(amount)},
            correlation_id=self.correlation_id,
        )
        response = self._to_response(transaction)
        await self.idempotency.complete(
            claim.record, resource_type=RESOURCE, resource_id=transaction.id, response=response
        )
        await self.session.flush()
        return response

    async def get(self, transaction_id: str) -> PaymentResponse:
        return self._to_response(await self._get(transaction_id))

    async def get_by_reference(self, reference: str) -> PaymentResponse:
        stmt = select(PaymentTransaction).where(PaymentTransaction.reference == reference)
        transaction = (await self.session.execute(stmt)).scalars().first()
        if transaction is None:
            raise ResourceNotFound(f"no payment transaction with reference {reference}")
        return self._to_response(transaction)

    async def apply_external_status(
        self, provider_transaction_id: str, status: str
    ) -> PaymentTransaction:
        """Used by the webhook handler after signature verification."""
        stmt = select(PaymentTransaction).where(
            PaymentTransaction.provider_transaction_id == provider_transaction_id
        )
        transaction = (await self.session.execute(stmt)).scalars().first()
        if transaction is None:
            raise ResourceNotFound(f"no payment for provider transaction {provider_transaction_id}")
        await self._apply_status(transaction, status)
        return transaction

    # -- internals -----------------------------------------------------------
    async def _apply_status(self, transaction: PaymentTransaction, status: str) -> None:
        if status == transaction.status:
            return
        if not can_transition(RAIL, transaction.status, status):
            logger.warning(
                "payment.ignored_transition",
                extra={
                    "resource_id": transaction.id,
                    "from_status": transaction.status,
                    "to_status": status,
                    "correlation_id": self.correlation_id,
                },
            )
            return
        previous = transaction.status
        transaction.status = status
        if status == "succeeded":
            transaction.settled_at = utcnow()
        await self.audit.record_event(
            rail=RAIL,
            resource_type=RESOURCE,
            resource_id=transaction.id,
            event_type="status_update",
            from_status=previous,
            to_status=status,
            correlation_id=self.correlation_id,
        )
        await self.session.flush()

    async def _get(self, transaction_id: str) -> PaymentTransaction:
        transaction = await self.session.get(PaymentTransaction, transaction_id)
        if transaction is None:
            raise ResourceNotFound(f"payment transaction {transaction_id} not found")
        return transaction

    async def _require(self, idempotency_key: str) -> PaymentTransaction:
        stmt = select(PaymentTransaction).where(
            PaymentTransaction.idempotency_key == idempotency_key
        )
        transaction = (await self.session.execute(stmt)).scalar_one_or_none()
        if transaction is None:  # pragma: no cover - defensive
            raise ResourceNotFound(f"no payment for idempotency key {idempotency_key}")
        return transaction

    async def _get_or_create(self, request: PaymentRequest) -> PaymentTransaction:
        stmt = select(PaymentTransaction).where(
            PaymentTransaction.idempotency_key == request.idempotency_key
        )
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing

        transaction = PaymentTransaction(
            idempotency_key=request.idempotency_key,
            correlation_id=self.correlation_id,
            reference=request.reference,
            source_system=request.source.system if request.source else "odoo",
            source_model=request.source.model if request.source else None,
            source_record_id=request.source.record_id if request.source else None,
            status="pending",
            currency=request.currency,
            amount=request.amount,
            provider=self.provider.name,
            request_payload=request.model_dump(mode="json"),
        )
        try:
            # add() inside the savepoint: see the note in IdempotencyService.claim.
            async with self.session.begin_nested():
                self.session.add(transaction)
                await self.session.flush()
        except IntegrityError:
            existing = (await self.session.execute(stmt)).scalar_one_or_none()
            if existing is None:  # pragma: no cover - defensive
                raise
            return existing

        await self.audit.record_event(
            rail=RAIL,
            resource_type=RESOURCE,
            resource_id=transaction.id,
            event_type="created",
            to_status="pending",
            detail={"reference": transaction.reference},
            correlation_id=self.correlation_id,
        )
        return transaction

    def _to_response(self, transaction: PaymentTransaction, *, replayed: bool = False) -> PaymentResponse:
        return PaymentResponse(
            id=transaction.id,
            idempotency_key=transaction.idempotency_key,
            reference=transaction.reference,
            status=transaction.status,
            provider=transaction.provider,
            provider_transaction_id=transaction.provider_transaction_id,
            checkout_url=transaction.checkout_url,
            amount=transaction.amount,
            refunded_amount=transaction.refunded_amount,
            currency=transaction.currency,
            last_error=transaction.last_error,
            attempt_count=transaction.attempt_count,
            correlation_id=transaction.correlation_id,
            replayed=replayed,
        )


def _now_ms() -> int:
    from datetime import datetime, timezone

    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


__all__ = ["PaymentService", "Decimal"]
