"""Mock payment provider - DEMONSTRATION ONLY.

Deliberately NOT a fake ArifPay. It implements the normalized contract with
three behaviours selected by MOCK_PAYMENT_MODE:

    auto_success  payment is authorised and settled immediately
    manual        payment stays pending until a webhook/verify arrives
    always_fail   provider rejects the payment

Provider identifiers are derived deterministically from the idempotency key,
so replaying a request yields the same provider transaction id.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.contracts import PaymentRequest
from app.domain.errors import ProviderPermanentError
from app.persistence.models import MockProviderState, utcnow
from app.providers.payment.base import (
    PaymentCreationResult,
    PaymentProvider,
    PaymentStatusResult,
    RefundResult,
)

STATE_PREFIX = "mock_payment:"


def deterministic_id(prefix: str, seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


class MockPaymentProvider(PaymentProvider):
    name = "mock"

    def __init__(self, settings: Settings, session: AsyncSession) -> None:
        self.settings = settings
        self.session = session

    async def create_payment(self, request: PaymentRequest) -> PaymentCreationResult:
        mode = self.settings.mock_payment_mode
        provider_txn = deterministic_id("mp", request.idempotency_key)

        if mode == "always_fail":
            raise ProviderPermanentError(
                "mock payment provider rejected the payment (MOCK_PAYMENT_MODE=always_fail)",
                provider=self.name,
                detail={"reference": request.reference},
            )

        status = "succeeded" if mode == "auto_success" else "pending"
        authorized_at = datetime.now(tz=UTC) if status == "succeeded" else None
        await self._remember(provider_txn, status)

        return PaymentCreationResult(
            provider_transaction_id=provider_txn,
            status=status,
            checkout_url=f"https://mock-payments.local/checkout/{provider_txn}",
            authorized_at=authorized_at,
            raw_response={
                "mock": True,
                "mode": mode,
                "reference": request.reference,
                "amount": f"{request.amount:.2f}",
                "currency": request.currency,
                "disclaimer": "DEMO PAYMENT - NO REAL FUNDS MOVE",
            },
        )

    async def verify_payment(self, reference: str) -> PaymentStatusResult:
        status = await self._recall(reference)
        if status is None:
            raise ProviderPermanentError(
                f"unknown payment reference {reference}", provider=self.name
            )
        return PaymentStatusResult(
            provider_transaction_id=reference,
            status=status,
            raw_response={"mock": True, "verified_at": datetime.now(tz=UTC).isoformat()},
        )

    async def refund(self, reference: str, amount: Decimal | None = None) -> RefundResult:
        status = await self._recall(reference)
        if status is None:
            raise ProviderPermanentError(
                f"unknown payment reference {reference}", provider=self.name
            )
        if status != "succeeded":
            raise ProviderPermanentError(
                f"payment {reference} is '{status}' and cannot be refunded", provider=self.name
            )
        return RefundResult(
            provider_refund_id=deterministic_id("mr", f"{reference}:{amount or 'full'}"),
            status="refunded",
            amount=amount or Decimal("0"),
            raw_response={"mock": True, "reference": reference},
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

    async def authorize(self, provider_txn: str) -> str:
        """Test/demo hook: simulate the customer completing a manual payment."""
        if await self._recall(provider_txn) is None:
            raise ProviderPermanentError(
                f"unknown payment reference {provider_txn}", provider=self.name
            )
        await self._remember(provider_txn, "succeeded")
        return "succeeded"
