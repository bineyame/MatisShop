"""Demo/administration endpoints.

These exist to make failure and recovery demonstrable on demand. They are
refused outright when GATEWAY_ENV=production - a switch that forces a fiscal
provider to fail has no business existing in a live deployment.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Path
from pydantic import BaseModel
from sqlalchemy import desc, select

from app.api.deps import AuthDep, SessionDep, SettingsDep
from app.domain.errors import ValidationError
from app.observability import get_logger
from app.persistence.models import IntegrationEvent, IntegrationRequest
from app.providers.delivery.mock import MockDeliveryProvider
from app.providers.fiscal.mock import MockFiscalProvider
from app.providers.payment.mock import MockPaymentProvider

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[AuthDep])


class FailureModeRequest(BaseModel):
    enabled: bool


def _guard(settings) -> None:  # noqa: ANN001
    if settings.is_production:
        raise ValidationError("demo controls are disabled when GATEWAY_ENV=production")


@router.get("/mock/fiscal/failure-mode")
async def get_fiscal_failure_mode(session: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    provider = MockFiscalProvider(settings, session)
    return {"provider": "mock", "failure_mode": await provider.failure_mode()}


@router.post("/mock/fiscal/failure-mode")
async def set_fiscal_failure_mode(
    session: SessionDep,
    settings: SettingsDep,
    payload: Annotated[FailureModeRequest, Body()],
) -> dict[str, Any]:
    """Force the mock fiscal provider to fail (or stop failing).

    Used by the demo: enable it, make a sale, watch the fiscal transaction go
    to `failed` while stock stays correct, then disable it and retry.
    """
    _guard(settings)
    provider = MockFiscalProvider(settings, session)
    await provider.set_failure_mode(payload.enabled)
    logger.warning("admin.fiscal_failure_mode", extra={"enabled": payload.enabled})
    return {"provider": "mock", "failure_mode": payload.enabled}


@router.post("/mock/payments/{provider_transaction_id}/authorize")
async def authorize_mock_payment(
    session: SessionDep,
    settings: SettingsDep,
    provider_transaction_id: Annotated[str, Path()],
) -> dict[str, Any]:
    """Simulate a customer completing a MOCK_PAYMENT_MODE=manual payment."""
    _guard(settings)
    provider = MockPaymentProvider(settings, session)
    status = await provider.authorize(provider_transaction_id)
    return {"provider_transaction_id": provider_transaction_id, "status": status}


@router.post("/mock/deliveries/{provider_transaction_id}/advance")
async def advance_mock_delivery(
    session: SessionDep,
    settings: SettingsDep,
    provider_transaction_id: Annotated[str, Path()],
    payload: Annotated[dict[str, str], Body()],
) -> dict[str, Any]:
    """Move a mock delivery to an explicit lifecycle state."""
    _guard(settings)
    status = payload.get("status")
    if not status:
        raise ValidationError("body must contain a 'status' field")
    provider = MockDeliveryProvider(settings, session)
    await provider.advance(provider_transaction_id, status)
    return {"provider_transaction_id": provider_transaction_id, "status": status}


@router.get("/audit/{resource_type}/{resource_id}")
async def get_audit_trail(
    session: SessionDep,
    resource_type: Annotated[str, Path()],
    resource_id: Annotated[str, Path()],
) -> dict[str, Any]:
    """Full audit trail for one resource.

    Answers, in one call: which ERP transaction triggered this, what we sent,
    when, which provider, what happened, how many attempts, what came back.
    """
    attempts = (
        (
            await session.execute(
                select(IntegrationRequest)
                .where(
                    IntegrationRequest.resource_type == resource_type,
                    IntegrationRequest.resource_id == resource_id,
                )
                .order_by(IntegrationRequest.created_at)
            )
        )
        .scalars()
        .all()
    )
    events = (
        (
            await session.execute(
                select(IntegrationEvent)
                .where(
                    IntegrationEvent.resource_type == resource_type,
                    IntegrationEvent.resource_id == resource_id,
                )
                .order_by(IntegrationEvent.created_at)
            )
        )
        .scalars()
        .all()
    )
    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "attempt_count": len(attempts),
        "attempts": [
            {
                "attempt": row.attempt,
                "operation": row.operation,
                "provider": row.provider,
                "outcome": row.outcome,
                "error": row.error,
                "duration_ms": row.duration_ms,
                "correlation_id": row.correlation_id,
                "created_at": row.created_at.isoformat(),
                "request_payload": row.request_payload,
                "response_payload": row.response_payload,
            }
            for row in attempts
        ],
        "transitions": [
            {
                "event_type": row.event_type,
                "from_status": row.from_status,
                "to_status": row.to_status,
                "detail": row.detail,
                "created_at": row.created_at.isoformat(),
            }
            for row in events
        ],
    }


@router.get("/audit/recent")
async def recent_activity(session: SessionDep, limit: int = 20) -> dict[str, Any]:
    rows = (
        (
            await session.execute(
                select(IntegrationRequest).order_by(desc(IntegrationRequest.created_at)).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "count": len(rows),
        "items": [
            {
                "rail": row.rail,
                "operation": row.operation,
                "provider": row.provider,
                "outcome": row.outcome,
                "attempt": row.attempt,
                "resource_id": row.resource_id,
                "idempotency_key": row.idempotency_key,
                "correlation_id": row.correlation_id,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
    }
