"""Extension point for REAL delivery providers.

Not implemented on purpose - no fabricated API behaviour.
See docs/integration-contracts.md#adding-a-real-delivery-provider.
"""

from __future__ import annotations

from app.config import Settings
from app.domain.contracts import DeliveryRequest
from app.domain.errors import ProviderNotConfigured
from app.providers.delivery.base import (
    DeliveryCancellationResult,
    DeliveryCreationResult,
    DeliveryProvider,
    DeliveryStatusResult,
)


class _UnimplementedDeliveryProvider(DeliveryProvider):
    required_settings: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()

    def __init__(self, settings: Settings, session=None) -> None:  # noqa: ANN001 - uniform factory
        self.settings = settings
        self.session = session

    def _unavailable(self) -> ProviderNotConfigured:
        return ProviderNotConfigured(
            f"delivery provider '{self.name}' is an extension point and is not implemented",
            provider=self.name,
            detail={
                "required_settings": list(self.required_settings),
                "blockers": list(self.blockers),
                "documentation": "docs/integration-contracts.md#adding-a-real-delivery-provider",
            },
        )

    async def create_delivery(self, request: DeliveryRequest) -> DeliveryCreationResult:
        raise self._unavailable()

    async def get_status(self, reference: str) -> DeliveryStatusResult:
        raise self._unavailable()

    async def cancel_delivery(self, reference: str) -> DeliveryCancellationResult:
        raise self._unavailable()


class KlikProvider(_UnimplementedDeliveryProvider):
    name = "klik"
    required_settings = ("KLIK_BASE_URL", "KLIK_API_KEY", "KLIK_MERCHANT_ID")
    blockers = (
        "merchant account with the courier",
        "current API reference and sandbox credentials",
        "coverage/pricing rules per city",
        "confirmed status callback contract",
    )
