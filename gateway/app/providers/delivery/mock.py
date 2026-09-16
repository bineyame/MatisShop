"""Mock delivery provider - DEMONSTRATION ONLY.

MOCK_DELIVERY_MODE:
    auto_advance  each status poll moves one step along the lifecycle
    manual        status only changes through a webhook
    always_fail   booking is rejected
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.contracts import DeliveryRequest
from app.domain.errors import ProviderPermanentError
from app.persistence.models import MockProviderState, utcnow
from app.providers.delivery.base import (
    LIFECYCLE,
    DeliveryCancellationResult,
    DeliveryCreationResult,
    DeliveryProvider,
    DeliveryStatusResult,
)

STATE_PREFIX = "mock_delivery:"
BASE_PRICE = Decimal("120.00")
PER_PACKAGE = Decimal("30.00")


class MockDeliveryProvider(DeliveryProvider):
    name = "mock"

    def __init__(self, settings: Settings, session: AsyncSession) -> None:
        self.settings = settings
        self.session = session

    async def create_delivery(self, request: DeliveryRequest) -> DeliveryCreationResult:
        if self.settings.mock_delivery_mode == "always_fail":
            raise ProviderPermanentError(
                "mock delivery provider rejected the booking (MOCK_DELIVERY_MODE=always_fail)",
                provider=self.name,
                detail={"reference": request.reference},
            )

        digest = hashlib.sha256(request.idempotency_key.encode("utf-8")).hexdigest()[:10].upper()
        provider_txn = f"md_{digest.lower()}"
        tracking = f"MD{digest}"
        price = BASE_PRICE + PER_PACKAGE * (len(request.packages) - 1)

        await self._remember(provider_txn, "created")
        return DeliveryCreationResult(
            provider_transaction_id=provider_txn,
            status="created",
            tracking_number=tracking,
            tracking_url=f"https://mock-delivery.local/track/{tracking}",
            price=price,
            raw_response={
                "mock": True,
                "reference": request.reference,
                "pickup": request.pickup.name,
                "dropoff": request.dropoff.name,
                "cash_on_delivery": f"{request.cash_on_delivery:.2f}",
                "disclaimer": "DEMO DELIVERY - NO COURIER IS DISPATCHED",
            },
        )

    async def get_status(self, reference: str) -> DeliveryStatusResult:
        current = await self._recall(reference)
        if current is None:
            raise ProviderPermanentError(
                f"unknown delivery reference {reference}", provider=self.name
            )
        if self.settings.mock_delivery_mode == "auto_advance" and current in LIFECYCLE:
            index = LIFECYCLE.index(current)
            if index < len(LIFECYCLE) - 1:
                current = LIFECYCLE[index + 1]
                await self._remember(reference, current)
        return DeliveryStatusResult(
            provider_transaction_id=reference,
            status=current,
            updated_at=datetime.now(tz=timezone.utc),
            raw_response={"mock": True, "mode": self.settings.mock_delivery_mode},
        )

    async def cancel_delivery(self, reference: str) -> DeliveryCancellationResult:
        current = await self._recall(reference)
        if current is None:
            raise ProviderPermanentError(
                f"unknown delivery reference {reference}", provider=self.name
            )
        if current == "delivered":
            raise ProviderPermanentError(
                f"delivery {reference} is already delivered", provider=self.name
            )
        await self._remember(reference, "cancelled")
        return DeliveryCancellationResult(
            provider_transaction_id=reference,
            status="cancelled",
            raw_response={"mock": True},
        )

    # -- tiny provider-side store -------------------------------------------
    async def _remember(self, provider_txn: str, status: str) -> None:
        key = f"{STATE_PREFIX}{provider_txn}"
        row = await self.session.get(MockProviderState, key)
        if row is None:
            self.session.add(MockProviderState(key=key, value=status))
        else:
            row.value = status
            row.updated_at = utcnow()
        await self.session.flush()

    async def _recall(self, provider_txn: str) -> str | None:
        row = await self.session.get(MockProviderState, f"{STATE_PREFIX}{provider_txn}")
        return row.value if row else None

    async def advance(self, provider_txn: str, status: str) -> str:
        """Test/demo hook used by the webhook simulator."""
        await self._remember(provider_txn, status)
        return status
