"""Delivery provider contract.

Normalized lifecycle every adapter maps onto:

    created -> assigned -> picked_up -> in_transit -> delivered
                    \\-> cancelled            \\-> failed
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.domain.contracts import DeliveryRequest

LIFECYCLE = ("created", "assigned", "picked_up", "in_transit", "delivered")


@dataclass
class DeliveryCreationResult:
    provider_transaction_id: str
    status: str
    tracking_number: str | None = None
    tracking_url: str | None = None
    price: Decimal = Decimal("0")
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeliveryStatusResult:
    provider_transaction_id: str
    status: str
    updated_at: datetime | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeliveryCancellationResult:
    provider_transaction_id: str
    status: str
    raw_response: dict[str, Any] = field(default_factory=dict)


class DeliveryProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    async def create_delivery(self, request: DeliveryRequest) -> DeliveryCreationResult:
        """Book a delivery. Must be idempotent on ``request.idempotency_key``."""

    @abstractmethod
    async def get_status(self, reference: str) -> DeliveryStatusResult:
        """Poll the provider for the current lifecycle state."""

    @abstractmethod
    async def cancel_delivery(self, reference: str) -> DeliveryCancellationResult:
        """Cancel a delivery that has not been completed."""
