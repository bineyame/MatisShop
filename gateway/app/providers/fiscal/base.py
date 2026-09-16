"""Fiscal provider contract.

Any accredited/approved fiscal system - the bundled mock today, a real
Ethiopian provider tomorrow - implements exactly this interface. Nothing
provider specific may appear in the signatures: the whole point is that
swapping the implementation is a configuration change, not an Odoo change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.contracts import FiscalDocumentRequest


@dataclass
class FiscalRegistrationResult:
    """Normalized successful registration."""

    provider_transaction_id: str
    irn: str
    qr_payload: str
    registered_at: datetime
    status: str = "registered"
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass
class FiscalCancellationResult:
    provider_transaction_id: str
    status: str
    cancelled_at: datetime
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass
class FiscalStatusResult:
    provider_transaction_id: str
    status: str
    irn: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


class FiscalProvider(ABC):
    """Interface every fiscal adapter implements."""

    #: stable provider identifier, stored on every record and audit row
    name: str = "abstract"

    @abstractmethod
    async def register(
        self, document: FiscalDocumentRequest, *, attempt: int = 1
    ) -> FiscalRegistrationResult:
        """Register one fiscal document.

        Implementations MUST treat ``document.idempotency_key`` as the logical
        identity of the document: repeating a call with the same key must
        return the original registration rather than creating a second one.

        Raise ``ProviderTransientError`` for anything worth retrying and
        ``ProviderPermanentError`` for a rejected document.
        """

    @abstractmethod
    async def cancel(self, reference: str, reason: str | None = None) -> FiscalCancellationResult:
        """Cancel/void a previously registered document."""

    @abstractmethod
    async def get_status(self, reference: str) -> FiscalStatusResult:
        """Read the provider view of a document - used for reconciliation."""
