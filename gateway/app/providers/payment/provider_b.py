"""Payment Provider B - adapter seam awaiting its API specification.

An API document for this provider exists but has not been supplied to the
repository yet. This file is the concrete place it lands, so that switching to
it later is a configuration change and an implementation of the four methods
below - nothing else in the platform moves.

**Nothing here invents endpoints or field names.** Every method raises until
the specification is mapped, because a plausible-looking fake would pass
review and fail in production.

To implement (see docs/providers.md and §38 of the brief):

1. read the provider's API document and keep its terminology;
2. map its request/response fields onto the normalized contract in
   ``app/domain/contracts.py`` - the mapping belongs HERE, never in Odoo;
3. classify every provider error as transient or permanent, so the gateway's
   existing retry logic keeps working unchanged;
4. honour ``request.idempotency_key``; if the provider has no idempotency
   header, deduplicate locally and flag it as a risk;
5. implement ``verify_payment`` properly - the gateway calls it before
   believing any webhook;
6. record the field-by-field mapping in ``docs/providers/payment-provider-b.md``.

Rename this module and its registry key to the provider's real name once the
document arrives; ``provider_b`` is a placeholder label, not a product.
"""

from __future__ import annotations

from decimal import Decimal

from app.config import Settings
from app.domain.contracts import PaymentRequest
from app.providers.config import require_settings
from app.providers.payment.base import (
    PaymentCreationResult,
    PaymentProvider,
    PaymentStatusResult,
    RefundResult,
)

#: Configuration this adapter will need. Checked at construction so that a
#: half-configured deployment fails immediately rather than at first payment.
REQUIRED_SETTINGS = (
    "PAYMENT_PROVIDER_B_BASE_URL",
    "PAYMENT_PROVIDER_B_API_KEY",
)

BLOCKERS = (
    "the provider's API document has not been added to this repository",
    "sandbox credentials for integration testing",
    "confirmed webhook signature scheme",
    "confirmation that the provider deduplicates on an idempotency key",
    "settlement and reconciliation rules",
)


class ProviderBPaymentProvider(PaymentProvider):
    name = "provider_b"
    required_settings = REQUIRED_SETTINGS
    blockers = BLOCKERS
    #: The webhook verifier needs this once the document is available.
    webhook_signature_scheme = "unknown - see the provider API document"

    def __init__(self, settings: Settings, session=None) -> None:  # noqa: ANN001
        self.settings = settings
        self.session = session
        # Fails loudly on selection, not silently at the first transaction.
        require_settings(
            settings,
            self.name,
            "payment",
            REQUIRED_SETTINGS,
            implemented=False,
            blockers=BLOCKERS,
        )

    async def create_payment(self, request: PaymentRequest) -> PaymentCreationResult:
        raise NotImplementedError  # pragma: no cover - __init__ always raises first

    async def verify_payment(self, reference: str) -> PaymentStatusResult:
        raise NotImplementedError  # pragma: no cover

    async def refund(self, reference: str, amount: Decimal | None = None) -> RefundResult:
        raise NotImplementedError  # pragma: no cover
