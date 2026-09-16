"""Provider registry.

Selecting a provider is configuration (FISCAL_PROVIDER / PAYMENT_PROVIDER /
DELIVERY_PROVIDER), never code that Odoo can see. Adding a provider means
adding one class and one line here.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.errors import ValidationError
from app.providers.delivery.base import DeliveryProvider
from app.providers.delivery.mock import MockDeliveryProvider
from app.providers.delivery.placeholders import KlikProvider
from app.providers.fiscal.base import FiscalProvider
from app.providers.fiscal.mock import MockFiscalProvider
from app.providers.fiscal.placeholders import AccreditedFiscalProvider, MoRFiscalProvider
from app.providers.payment.base import PaymentProvider
from app.providers.payment.mock import MockPaymentProvider
from app.providers.payment.placeholders import ArifPayProvider, ChapaProvider, TelebirrProvider

FISCAL_PROVIDERS: dict[str, Callable[[Settings, AsyncSession], FiscalProvider]] = {
    "mock": MockFiscalProvider,
    "mor": MoRFiscalProvider,
    "accredited": AccreditedFiscalProvider,
}

PAYMENT_PROVIDERS: dict[str, Callable[[Settings, AsyncSession], PaymentProvider]] = {
    "mock": MockPaymentProvider,
    "arifpay": ArifPayProvider,
    "chapa": ChapaProvider,
    "telebirr": TelebirrProvider,
}

DELIVERY_PROVIDERS: dict[str, Callable[[Settings, AsyncSession], DeliveryProvider]] = {
    "mock": MockDeliveryProvider,
    "klik": KlikProvider,
}


def _build(registry: dict, rail: str, name: str, settings: Settings, session: AsyncSession):
    factory = registry.get(name)
    if factory is None:
        raise ValidationError(
            f"unknown {rail} provider '{name}'",
            detail={"available": sorted(registry)},
        )
    return factory(settings, session)


def get_fiscal_provider(
    settings: Settings, session: AsyncSession, name: str | None = None
) -> FiscalProvider:
    return _build(FISCAL_PROVIDERS, "fiscal", name or settings.fiscal_provider, settings, session)


def get_payment_provider(
    settings: Settings, session: AsyncSession, name: str | None = None
) -> PaymentProvider:
    return _build(PAYMENT_PROVIDERS, "payment", name or settings.payment_provider, settings, session)


def get_delivery_provider(
    settings: Settings, session: AsyncSession, name: str | None = None
) -> DeliveryProvider:
    return _build(
        DELIVERY_PROVIDERS, "delivery", name or settings.delivery_provider, settings, session
    )


def describe_providers(settings: Settings) -> dict[str, str]:
    """Used by the health endpoint to show which rails are wired to what."""
    return {
        "fiscal": settings.fiscal_provider,
        "payment": settings.payment_provider,
        "delivery": settings.delivery_provider,
    }
