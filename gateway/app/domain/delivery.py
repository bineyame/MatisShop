"""Delivery rail service.

Lightweight by design: the delivery seam exists to prove a courier is
swappable, not to model logistics. Same idempotency and audit guarantees.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.audit import AuditService
from app.domain.contracts import DeliveryRequest, DeliveryResponse
from app.domain.errors import (
    IdempotencyConflict,
    OperationInProgress,
    ProviderError,
    ResourceNotFound,
)
from app.domain.idempotency import IdempotencyService, compute_request_hash
from app.domain.state_machine import assert_transition, can_transition
from app.observability import get_logger
from app.persistence.models import DeliveryOrder
from app.providers.delivery.base import DeliveryProvider

logger = get_logger(__name__)

RAIL = "delivery"
SCOPE_CREATE = "delivery.create"
RESOURCE = "delivery_order"


class DeliveryService:
    def __init__(
        self,
        session: AsyncSession,
        provider: DeliveryProvider,
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

    async def create(self, request: DeliveryRequest) -> DeliveryResponse:
        claim = await self.idempotency.claim(
            scope=SCOPE_CREATE,
            key=request.idempotency_key,
            request_hash=compute_request_hash(request),
            correlation_id=self.correlation_id,
        )
        if claim.state == "conflict":
            raise IdempotencyConflict("idempotency key already used with a different request body")
        if claim.state == "replay":
            return self._to_response(await self._require(request.idempotency_key), replayed=True)
        if claim.state == "in_progress":
            raise OperationInProgress("this delivery is already being booked")

        order = await self._get_or_create(request)
        if order.provider_transaction_id:
            response = self._to_response(order, replayed=True)
            await self.idempotency.complete(
                claim.record, resource_type=RESOURCE, resource_id=order.id, response=response
            )
            return response

        order.attempt_count += 1
        started = _now_ms()
        try:
            result = await self.provider.create_delivery(request)
        except ProviderError as exc:
            await self.audit.record_attempt(
                rail=RAIL,
                operation="create_delivery",
                provider=self.provider.name,
                outcome="transient_error" if getattr(exc, "retryable", False) else "permanent_error",
                attempt=order.attempt_count,
                duration_ms=_now_ms() - started,
                resource_type=RESOURCE,
                resource_id=order.id,
                idempotency_key=order.idempotency_key,
                correlation_id=self.correlation_id,
                request_payload=request.model_dump(mode="json"),
                error=str(exc),
            )
            previous = order.status
            assert_transition(RAIL, previous, "failed")
            order.status = "failed"
            order.last_error = str(exc)
            await self.audit.record_event(
                rail=RAIL,
                resource_type=RESOURCE,
                resource_id=order.id,
                event_type="failed",
                from_status=previous,
                to_status="failed",
                detail={"error": str(exc)},
                correlation_id=self.correlation_id,
            )
            await self.idempotency.fail(
                claim.record, error=str(exc), resource_type=RESOURCE, resource_id=order.id
            )
            await self.session.flush()
            return self._to_response(order)

        order.provider_transaction_id = result.provider_transaction_id
        order.tracking_number = result.tracking_number
        order.tracking_url = result.tracking_url
        order.price = result.price
        order.provider_response = result.raw_response
        await self.audit.record_attempt(
            rail=RAIL,
            operation="create_delivery",
            provider=self.provider.name,
            outcome="success",
            attempt=order.attempt_count,
            duration_ms=_now_ms() - started,
            resource_type=RESOURCE,
            resource_id=order.id,
            idempotency_key=order.idempotency_key,
            correlation_id=self.correlation_id,
            request_payload=request.model_dump(mode="json"),
            response_payload=result.raw_response,
        )
        response = self._to_response(order)
        await self.idempotency.complete(
            claim.record, resource_type=RESOURCE, resource_id=order.id, response=response
        )
        await self.session.flush()
        return response

    async def refresh_status(self, order_id: str) -> DeliveryResponse:
        order = await self._get(order_id)
        if not order.provider_transaction_id:
            return self._to_response(order)
        result = await self.provider.get_status(order.provider_transaction_id)
        await self._apply_status(order, result.status)
        return self._to_response(order)

    async def cancel(self, order_id: str) -> DeliveryResponse:
        order = await self._get(order_id)
        assert_transition(RAIL, order.status, "cancelled")
        if order.provider_transaction_id:
            await self.provider.cancel_delivery(order.provider_transaction_id)
        previous = order.status
        order.status = "cancelled"
        order.cancelled_at = _utcnow()
        await self.audit.record_event(
            rail=RAIL,
            resource_type=RESOURCE,
            resource_id=order.id,
            event_type="cancelled",
            from_status=previous,
            to_status="cancelled",
            correlation_id=self.correlation_id,
        )
        await self.session.flush()
        return self._to_response(order)

    async def get(self, order_id: str) -> DeliveryResponse:
        return self._to_response(await self._get(order_id))

    async def apply_external_status(self, provider_transaction_id: str, status: str) -> DeliveryOrder:
        stmt = select(DeliveryOrder).where(
            DeliveryOrder.provider_transaction_id == provider_transaction_id
        )
        order = (await self.session.execute(stmt)).scalars().first()
        if order is None:
            raise ResourceNotFound(f"no delivery for provider transaction {provider_transaction_id}")
        await self._apply_status(order, status)
        return order

    # -- internals -----------------------------------------------------------
    async def _apply_status(self, order: DeliveryOrder, status: str) -> None:
        if status == order.status:
            return
        if not can_transition(RAIL, order.status, status):
            logger.warning(
                "delivery.ignored_transition",
                extra={
                    "resource_id": order.id,
                    "from_status": order.status,
                    "to_status": status,
                },
            )
            return
        previous = order.status
        order.status = status
        if status == "delivered":
            order.delivered_at = _utcnow()
        await self.audit.record_event(
            rail=RAIL,
            resource_type=RESOURCE,
            resource_id=order.id,
            event_type="status_update",
            from_status=previous,
            to_status=status,
            correlation_id=self.correlation_id,
        )
        await self.session.flush()

    async def _get(self, order_id: str) -> DeliveryOrder:
        order = await self.session.get(DeliveryOrder, order_id)
        if order is None:
            raise ResourceNotFound(f"delivery order {order_id} not found")
        return order

    async def _require(self, idempotency_key: str) -> DeliveryOrder:
        stmt = select(DeliveryOrder).where(DeliveryOrder.idempotency_key == idempotency_key)
        order = (await self.session.execute(stmt)).scalar_one_or_none()
        if order is None:  # pragma: no cover - defensive
            raise ResourceNotFound(f"no delivery for idempotency key {idempotency_key}")
        return order

    async def _get_or_create(self, request: DeliveryRequest) -> DeliveryOrder:
        stmt = select(DeliveryOrder).where(DeliveryOrder.idempotency_key == request.idempotency_key)
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing

        order = DeliveryOrder(
            idempotency_key=request.idempotency_key,
            correlation_id=self.correlation_id,
            reference=request.reference,
            source_system=request.source.system if request.source else "odoo",
            source_model=request.source.model if request.source else None,
            source_record_id=request.source.record_id if request.source else None,
            status="created",
            provider=self.provider.name,
            currency=request.currency,
            request_payload=request.model_dump(mode="json"),
        )
        try:
            # add() inside the savepoint: see the note in IdempotencyService.claim.
            async with self.session.begin_nested():
                self.session.add(order)
                await self.session.flush()
        except IntegrityError:
            existing = (await self.session.execute(stmt)).scalar_one_or_none()
            if existing is None:  # pragma: no cover - defensive
                raise
            return existing

        await self.audit.record_event(
            rail=RAIL,
            resource_type=RESOURCE,
            resource_id=order.id,
            event_type="created",
            to_status="created",
            detail={"reference": order.reference},
            correlation_id=self.correlation_id,
        )
        return order

    def _to_response(self, order: DeliveryOrder, *, replayed: bool = False) -> DeliveryResponse:
        return DeliveryResponse(
            id=order.id,
            idempotency_key=order.idempotency_key,
            reference=order.reference,
            status=order.status,
            provider=order.provider,
            provider_transaction_id=order.provider_transaction_id,
            tracking_number=order.tracking_number,
            tracking_url=order.tracking_url,
            price=order.price,
            currency=order.currency,
            last_error=order.last_error,
            correlation_id=order.correlation_id,
            replayed=replayed,
        )


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _now_ms() -> int:
    return int(datetime.now(tz=UTC).timestamp() * 1000)
