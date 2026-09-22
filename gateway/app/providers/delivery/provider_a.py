"""Delivery Provider A - adapter seam awaiting its API specification.

An API document for this courier exists but has not been supplied to the
repository yet. This file is the concrete place it lands.

**Nothing here invents endpoints or field names.**

To implement (see docs/providers.md):

1. read the courier's API document and keep its terminology;
2. map its statuses onto the seven normalized lifecycle states in
   ``app/providers/delivery/base.py`` - the mapping belongs HERE;
3. classify errors as transient or permanent so retries keep working;
4. honour ``request.idempotency_key`` so a retried booking does not dispatch
   two couriers;
5. record the mapping in ``docs/providers/delivery-provider-a.md``.

Rename this module and its registry key to the courier's real name once the
document arrives.
"""

from __future__ import annotations

from app.config import Settings
from app.domain.contracts import DeliveryRequest
from app.providers.config import require_settings
from app.providers.delivery.base import (
    DeliveryCancellationResult,
    DeliveryCreationResult,
    DeliveryProvider,
    DeliveryStatusResult,
)

REQUIRED_SETTINGS = (
    "DELIVERY_PROVIDER_A_BASE_URL",
    "DELIVERY_PROVIDER_A_API_KEY",
    "DELIVERY_PROVIDER_A_MERCHANT_ID",
)

BLOCKERS = (
    "the courier's API document has not been added to this repository",
    "sandbox credentials for integration testing",
    "coverage and pricing rules per city",
    "confirmed status callback contract",
)


class ProviderADeliveryProvider(DeliveryProvider):
    name = "provider_a"
    required_settings = REQUIRED_SETTINGS
    blockers = BLOCKERS

    def __init__(self, settings: Settings, session=None) -> None:  # noqa: ANN001
        self.settings = settings
        self.session = session
        require_settings(
            settings,
            self.name,
            "delivery",
            REQUIRED_SETTINGS,
            implemented=False,
            blockers=BLOCKERS,
        )

    async def create_delivery(self, request: DeliveryRequest) -> DeliveryCreationResult:
        raise NotImplementedError  # pragma: no cover - __init__ always raises first

    async def get_status(self, reference: str) -> DeliveryStatusResult:
        raise NotImplementedError  # pragma: no cover

    async def cancel_delivery(self, reference: str) -> DeliveryCancellationResult:
        raise NotImplementedError  # pragma: no cover
