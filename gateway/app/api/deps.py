"""FastAPI dependencies: authentication, correlation, service wiring."""

from __future__ import annotations

import hmac
import uuid
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.domain.delivery import DeliveryService
from app.domain.errors import AuthenticationError
from app.domain.fiscal import FiscalService
from app.domain.payments import PaymentService
from app.persistence.database import get_session
from app.providers.registry import (
    get_delivery_provider,
    get_fiscal_provider,
    get_payment_provider,
)

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def correlation_id(
    request: Request,
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-Id")] = None,
) -> str:
    """One id that ties an ERP action to every gateway log line and audit row."""
    value = x_correlation_id or getattr(request.state, "correlation_id", None) or uuid.uuid4().hex
    request.state.correlation_id = value
    return value


CorrelationDep = Annotated[str, Depends(correlation_id)]


async def require_api_key(
    settings: SettingsDep,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Shared-secret authentication for ERP clients.

    Development-grade on purpose: production should terminate mTLS or OAuth at
    the edge. See docs/architecture.md#production-hardening.
    """
    if not x_api_key or not hmac.compare_digest(x_api_key, settings.gateway_api_key):
        raise AuthenticationError("missing or invalid X-API-Key header")


AuthDep = Depends(require_api_key)


async def fiscal_service(
    session: SessionDep,
    settings: SettingsDep,
    correlation: CorrelationDep,
    x_provider: Annotated[str | None, Header(alias="X-Provider")] = None,
) -> FiscalService:
    provider = get_fiscal_provider(settings, session, x_provider)
    return FiscalService(session, provider, settings, correlation_id=correlation)


async def payment_service(
    session: SessionDep,
    settings: SettingsDep,
    correlation: CorrelationDep,
    x_provider: Annotated[str | None, Header(alias="X-Provider")] = None,
) -> PaymentService:
    provider = get_payment_provider(settings, session, x_provider)
    return PaymentService(session, provider, settings, correlation_id=correlation)


async def delivery_service(
    session: SessionDep,
    settings: SettingsDep,
    correlation: CorrelationDep,
    x_provider: Annotated[str | None, Header(alias="X-Provider")] = None,
) -> DeliveryService:
    provider = get_delivery_provider(settings, session, x_provider)
    return DeliveryService(session, provider, settings, correlation_id=correlation)


FiscalServiceDep = Annotated[FiscalService, Depends(fiscal_service)]
PaymentServiceDep = Annotated[PaymentService, Depends(payment_service)]
DeliveryServiceDep = Annotated[DeliveryService, Depends(delivery_service)]
