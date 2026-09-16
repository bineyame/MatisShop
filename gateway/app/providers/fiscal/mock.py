"""Mock Ethiopian fiscal provider - DEMONSTRATION ONLY.

This is NOT the Ministry of Revenue protocol and produces NO legally valid
fiscal registration. It exists to make the architecture runnable end to end
and to exercise the properties that a real integration must have:

  * its own registration ledger, keyed by idempotency key, so a repeated
    submission returns the ORIGINAL IRN instead of creating a second one;
  * a deterministic, human-readable IRN sequence;
  * a QR payload shaped like a real fiscal QR (compact, base64 encoded);
  * an injectable failure mode so retry/recovery can be demonstrated.

The IRN format below is invented for the demo. Do not read anything official
into it. See docs/fiscal-integration.md.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.contracts import FiscalDocumentRequest
from app.domain.errors import ProviderPermanentError, ProviderTransientError
from app.persistence.models import MockFiscalRegistration, MockProviderState, utcnow
from app.providers.fiscal.base import (
    FiscalCancellationResult,
    FiscalProvider,
    FiscalRegistrationResult,
    FiscalStatusResult,
)

FAILURE_MODE_KEY = "mock_fiscal_failure_mode"


class MockFiscalProvider(FiscalProvider):
    name = "mock"

    def __init__(self, settings: Settings, session: AsyncSession) -> None:
        self.settings = settings
        self.session = session

    # -- failure injection ---------------------------------------------------
    async def failure_mode(self) -> bool:
        """Runtime override wins over the environment default."""
        row = await self.session.get(MockProviderState, FAILURE_MODE_KEY)
        if row is not None:
            return row.value.lower() in ("1", "true", "yes", "on")
        return self.settings.mock_fiscal_failure_mode

    async def set_failure_mode(self, enabled: bool) -> bool:
        row = await self.session.get(MockProviderState, FAILURE_MODE_KEY)
        if row is None:
            row = MockProviderState(key=FAILURE_MODE_KEY, value=str(enabled).lower())
            self.session.add(row)
        else:
            row.value = str(enabled).lower()
            row.updated_at = utcnow()
        await self.session.flush()
        return enabled

    # -- provider contract ---------------------------------------------------
    async def register(
        self, document: FiscalDocumentRequest, *, attempt: int = 1
    ) -> FiscalRegistrationResult:
        key = document.idempotency_key

        # 1. Provider-side deduplication. This is what makes a lost response
        #    safe: the caller retries, we return the original registration.
        existing = await self._find(key)
        if existing is not None:
            if existing.status == "cancelled":
                raise ProviderPermanentError(
                    f"document {existing.irn} was cancelled and cannot be re-registered",
                    provider=self.name,
                )
            return self._to_result(existing, replayed=True)

        # 2. Injected failure, evaluated AFTER dedup so a registered document
        #    keeps replaying cleanly even while the provider is "down".
        if await self.failure_mode():
            raise ProviderTransientError(
                "mock fiscal provider is in forced failure mode (MOCK_FISCAL_FAILURE_MODE)",
                provider=self.name,
                detail={"injected": True, "attempt": attempt},
            )

        # 3. Minimal provider-side validation, mirroring what a real fiscal
        #    system rejects outright rather than asking you to retry.
        if document.totals.grand_total <= Decimal("0"):
            raise ProviderPermanentError(
                "grand_total must be greater than zero", provider=self.name
            )
        if not document.seller.tin:
            raise ProviderPermanentError("seller TIN is required", provider=self.name)

        registered_at = datetime.now(tz=timezone.utc)
        row = MockFiscalRegistration(
            idempotency_key=key,
            provider_transaction_id="",
            irn="",
            qr_payload="",
            document_number=document.document.number,
            grand_total=document.totals.grand_total,
            status="registered",
            registered_at=registered_at,
        )
        try:
            # add() inside the savepoint: see the note in IdempotencyService.claim.
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()  # allocates row.seq
        except IntegrityError:
            # Concurrent duplicate: re-read and replay the winner.
            existing = await self._find(key)
            if existing is None:  # pragma: no cover - defensive
                raise
            return self._to_result(existing, replayed=True)

        prefix = self.settings.mock_fiscal_irn_prefix
        row.irn = f"{prefix}-{registered_at.year}-{row.seq:06d}"
        row.provider_transaction_id = f"fp_{row.seq:08d}"
        row.qr_payload = self._build_qr_payload(document, row.irn, registered_at)
        await self.session.flush()
        return self._to_result(row, replayed=False)

    async def cancel(self, reference: str, reason: str | None = None) -> FiscalCancellationResult:
        row = await self._find_by_reference(reference)
        if row is None:
            raise ProviderPermanentError(f"unknown fiscal reference {reference}", provider=self.name)
        if row.status != "cancelled":
            row.status = "cancelled"
            row.cancelled_at = datetime.now(tz=timezone.utc)
            await self.session.flush()
        return FiscalCancellationResult(
            provider_transaction_id=row.provider_transaction_id,
            status="cancelled",
            cancelled_at=row.cancelled_at or datetime.now(tz=timezone.utc),
            raw_response={"irn": row.irn, "reason": reason, "mock": True},
        )

    async def get_status(self, reference: str) -> FiscalStatusResult:
        row = await self._find_by_reference(reference)
        if row is None:
            raise ProviderPermanentError(f"unknown fiscal reference {reference}", provider=self.name)
        return FiscalStatusResult(
            provider_transaction_id=row.provider_transaction_id,
            status=row.status,
            irn=row.irn,
            raw_response={"mock": True, "registered_at": row.registered_at.isoformat()},
        )

    # -- helpers -------------------------------------------------------------
    def _build_qr_payload(
        self, document: FiscalDocumentRequest, irn: str, registered_at: datetime
    ) -> str:
        """Compact pipe-delimited payload, base64 encoded.

        Real fiscal QR codes carry a signed, provider-defined payload. The
        signing seam lives in the gateway so a real provider can sign here
        without touching Odoo.
        """
        seller_tin = document.seller.tin or ""
        buyer_tin = (document.buyer.tin if document.buyer else "") or ""
        raw = "|".join(
            [
                "ETDEMO1",
                irn,
                seller_tin,
                buyer_tin,
                document.document.number or "",
                registered_at.strftime("%Y%m%d%H%M%S"),
                f"{document.totals.grand_total:.2f}",
                f"{document.totals.tax_total:.2f}",
                document.totals.currency,
            ]
        )
        return base64.b64encode(raw.encode("utf-8")).decode("ascii")

    def _to_result(self, row: MockFiscalRegistration, *, replayed: bool) -> FiscalRegistrationResult:
        return FiscalRegistrationResult(
            provider_transaction_id=row.provider_transaction_id,
            irn=row.irn,
            qr_payload=row.qr_payload,
            registered_at=row.registered_at,
            status=row.status,
            raw_response={
                "mock": True,
                "replayed": replayed,
                "seq": row.seq,
                "irn": row.irn,
                "provider_transaction_id": row.provider_transaction_id,
                "status": row.status,
                "registered_at": row.registered_at.isoformat(),
                "disclaimer": "DEMO FISCAL REGISTRATION - NOT PRODUCTION CERTIFICATION",
            },
        )

    async def _find(self, idempotency_key: str) -> MockFiscalRegistration | None:
        stmt = select(MockFiscalRegistration).where(
            MockFiscalRegistration.idempotency_key == idempotency_key
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _find_by_reference(self, reference: str) -> MockFiscalRegistration | None:
        """Accept either the IRN or the provider transaction id."""
        stmt = select(MockFiscalRegistration).where(
            (MockFiscalRegistration.irn == reference)
            | (MockFiscalRegistration.provider_transaction_id == reference)
        )
        return (await self.session.execute(stmt)).scalars().first()
