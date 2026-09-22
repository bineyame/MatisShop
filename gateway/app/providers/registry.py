"""Provider registry.

Selecting a provider is configuration (FISCAL_PROVIDER / PAYMENT_PROVIDER /
DELIVERY_PROVIDER), never code that Odoo can see. Adding a provider means
adding one class and one line here.

Two rules this module enforces:

* **an unknown provider name is rejected**, with the valid names listed;
* **a real provider without its credentials fails loudly** and never falls
  back to the mock - see ``validate_provider_configuration``.

The business workflow is identical whichever provider is active. If you ever
find yourself writing ``if provider == "a": ... elif provider == "b": ...``
outside an adapter, the difference belongs inside that adapter instead.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.errors import ProviderNotConfigured, ValidationError
from app.observability import get_logger
from app.providers.delivery.base import DeliveryProvider
from app.providers.delivery.mock import MockDeliveryProvider
from app.providers.delivery.provider_a import ProviderADeliveryProvider
from app.providers.fiscal.base import FiscalProvider
from app.providers.fiscal.mock import MockFiscalProvider
from app.providers.fiscal.placeholders import AccreditedFiscalProvider, MoRFiscalProvider
from app.providers.payment.base import PaymentProvider
from app.providers.payment.mock import MockPaymentProvider
from app.providers.payment.provider_a import ProviderAPaymentProvider
from app.providers.payment.provider_b import ProviderBPaymentProvider

logger = get_logger(__name__)

FISCAL_PROVIDERS: dict[str, Callable[[Settings, AsyncSession], FiscalProvider]] = {
    "mock": MockFiscalProvider,
    "mor": MoRFiscalProvider,
    "accredited": AccreditedFiscalProvider,
}

PAYMENT_PROVIDERS: dict[str, Callable[[Settings, AsyncSession], PaymentProvider]] = {
    "mock": MockPaymentProvider,
    # Adapter seams awaiting their API documents. Rename these keys to the
    # providers' real names when the documents arrive (docs/providers.md).
    "provider_a": ProviderAPaymentProvider,
    "provider_b": ProviderBPaymentProvider,
}

DELIVERY_PROVIDERS: dict[str, Callable[[Settings, AsyncSession], DeliveryProvider]] = {
    "mock": MockDeliveryProvider,
    "provider_a": ProviderADeliveryProvider,
}

REGISTRIES = {
    "fiscal": FISCAL_PROVIDERS,
    "payment": PAYMENT_PROVIDERS,
    "delivery": DELIVERY_PROVIDERS,
}


def _build(registry: dict, rail: str, name: str, settings: Settings, session: AsyncSession):
    factory = registry.get(name)
    if factory is None:
        raise ValidationError(
            f"unknown {rail} provider '{name}'",
            detail={"rail": rail, "available": sorted(registry)},
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


def validate_provider_configuration(settings: Settings) -> None:
    """Fail fast at startup on a provider that cannot possibly work.

    Called from the application lifespan. Refusing to start is deliberate: a
    gateway that boots with ``PAYMENT_PROVIDER=provider_a`` and no credentials
    would accept payment requests it cannot fulfil, and the operator would
    discover that from a customer rather than from a deploy.

    Mock providers need no configuration and always pass.
    """
    problems: list[str] = []

    for rail, registry in REGISTRIES.items():
        name = getattr(settings, f"{rail}_provider")
        if name not in registry:
            problems.append(
                f"{rail.upper()}_PROVIDER='{name}' is not a known provider "
                f"(available: {', '.join(sorted(registry))})"
            )
            continue
        if name == "mock":
            continue
        # Constructing the adapter is what validates its configuration; no
        # database session is needed for that check.
        try:
            registry[name](settings, None)
        except ProviderNotConfigured as exc:
            detail = exc.detail or {}
            missing = detail.get("missing_settings") or detail.get("required_settings") or []
            problems.append(f"{rail.upper()}_PROVIDER='{name}': {exc.message} ({', '.join(missing)})")
        except Exception as exc:  # noqa: BLE001 - any construction failure is fatal here
            problems.append(f"{rail.upper()}_PROVIDER='{name}' could not be initialised: {exc}")

    if problems:
        message = "invalid provider configuration:\n  - " + "\n  - ".join(problems)
        logger.error("gateway.provider_configuration_invalid", extra={"problems": problems})
        raise RuntimeError(message)

    logger.info("gateway.providers_validated", extra=describe_providers(settings))
