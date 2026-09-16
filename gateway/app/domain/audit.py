"""Integration audit trail.

Everything written here is append-only. A failed attempt is never overwritten
by a later successful one: "was it retried, and how often?" must stay
answerable (ADR-007).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.observability import get_logger, redact
from app.persistence.models import IntegrationEvent, IntegrationRequest

logger = get_logger(__name__)


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_attempt(
        self,
        *,
        rail: str,
        operation: str,
        provider: str,
        outcome: str,
        attempt: int,
        duration_ms: int,
        resource_type: str | None = None,
        resource_id: str | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
        request_payload: dict[str, Any] | None = None,
        response_payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> IntegrationRequest:
        row = IntegrationRequest(
            rail=rail,
            operation=operation,
            provider=provider,
            outcome=outcome,
            attempt=attempt,
            duration_ms=duration_ms,
            resource_type=resource_type,
            resource_id=resource_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            request_payload=redact(request_payload) if request_payload is not None else None,
            response_payload=redact(response_payload) if response_payload is not None else None,
            error=error,
        )
        self.session.add(row)
        await self.session.flush()
        logger.info(
            "integration.attempt",
            extra={
                "rail": rail,
                "operation": operation,
                "provider": provider,
                "outcome": outcome,
                "attempt": attempt,
                "duration_ms": duration_ms,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "idempotency_key": idempotency_key,
                "correlation_id": correlation_id,
                "error": error,
            },
        )
        return row

    async def record_event(
        self,
        *,
        rail: str,
        resource_type: str,
        resource_id: str,
        event_type: str,
        from_status: str | None = None,
        to_status: str | None = None,
        detail: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> IntegrationEvent:
        row = IntegrationEvent(
            rail=rail,
            resource_type=resource_type,
            resource_id=resource_id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            detail=redact(detail) if detail is not None else None,
            correlation_id=correlation_id,
        )
        self.session.add(row)
        await self.session.flush()
        logger.info(
            "integration.transition",
            extra={
                "rail": rail,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "event_type": event_type,
                "from_status": from_status,
                "to_status": to_status,
                "correlation_id": correlation_id,
            },
        )
        return row
