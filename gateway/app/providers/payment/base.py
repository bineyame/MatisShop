"""Payment provider contract.

ArifPay, Chapa, Telebirr or a card acquirer all sit behind this interface.
Odoo never learns which one is active.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.domain.contracts import PaymentRequest


@dataclass
class PaymentCreationResult:
    provider_transaction_id: str
    #: pending | authorized | succeeded | failed
    status: str
    checkout_url: str | None = None
    authorized_at: datetime | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaymentStatusResult:
    provider_transaction_id: str
    status: str
    amount: Decimal | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass
class RefundResult:
    provider_refund_id: str
    status: str
    amount: Decimal
    raw_response: dict[str, Any] = field(default_factory=dict)


class PaymentProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    async def create_payment(self, request: PaymentRequest) -> PaymentCreationResult:
        """Initiate a payment. Must be idempotent on ``request.idempotency_key``."""

    @abstractmethod
    async def verify_payment(self, reference: str) -> PaymentStatusResult:
        """Read authoritative payment state from the provider.

        Never trust a webhook alone for money: verify before marking paid.
        """

    @abstractmethod
    async def refund(self, reference: str, amount: Decimal | None = None) -> RefundResult:
        """Full refund when ``amount`` is None, otherwise partial."""
