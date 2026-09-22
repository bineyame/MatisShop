"""Extension points for REAL Ethiopian fiscal providers.

These classes are intentionally NOT implemented. Ethiopia's Ministry of
Revenue (MoR) electronic invoicing interface and the interfaces of accredited
fiscal service providers are not public documents we can implement against,
and inventing endpoints would produce code that looks finished and is wrong.

What a real implementation needs, and where it goes:

    1. Credentials + endpoint             -> environment variables only
    2. Authentication (token / mTLS)      -> _authenticate(), inside this class
    3. Payload mapping                    -> map FiscalDocumentRequest to the
                                             provider schema, inside this class
    4. Digital signing of the QR payload  -> _sign(), inside this class
    5. Error classification               -> transient vs permanent, so the
                                             existing retry logic keeps working

Nothing in Odoo changes when one of these is finished: set FISCAL_PROVIDER to
the new name and restart the gateway. That is the whole point of the boundary.

See docs/integration-contracts.md#adding-a-real-fiscal-provider.
"""

from __future__ import annotations

from app.config import Settings
from app.domain.contracts import FiscalDocumentRequest
from app.domain.errors import ProviderNotConfigured
from app.providers.config import require_settings
from app.providers.fiscal.base import (
    FiscalCancellationResult,
    FiscalProvider,
    FiscalRegistrationResult,
    FiscalStatusResult,
)


class _UnimplementedFiscalProvider(FiscalProvider):
    #: environment variables a real implementation must read
    required_settings: tuple[str, ...] = ()
    #: what still has to be obtained from the authority/provider
    blockers: tuple[str, ...] = ()

    def __init__(self, settings: Settings, session=None) -> None:  # noqa: ANN001 - uniform factory
        self.settings = settings
        self.session = session
        # Validate on SELECTION, not on first use. Otherwise a gateway
        # configured with FISCAL_PROVIDER=mor starts cleanly and only fails at
        # the first sale of the day - exactly the silent misconfiguration the
        # fail-fast rule exists to prevent.
        require_settings(
            settings,
            self.name,
            "fiscal",
            self.required_settings,
            implemented=False,
            blockers=self.blockers,
        )

    def _unavailable(self) -> ProviderNotConfigured:
        return ProviderNotConfigured(
            f"fiscal provider '{self.name}' is an extension point and is not implemented",
            provider=self.name,
            detail={
                "required_settings": list(self.required_settings),
                "blockers": list(self.blockers),
                "documentation": "docs/integration-contracts.md#adding-a-real-fiscal-provider",
            },
        )

    async def register(
        self, document: FiscalDocumentRequest, *, attempt: int = 1
    ) -> FiscalRegistrationResult:
        raise self._unavailable()

    async def cancel(self, reference: str, reason: str | None = None) -> FiscalCancellationResult:
        raise self._unavailable()

    async def get_status(self, reference: str) -> FiscalStatusResult:
        raise self._unavailable()


class MoRFiscalProvider(_UnimplementedFiscalProvider):
    """Direct Ministry of Revenue electronic invoicing."""

    name = "mor"
    required_settings = (
        "MOR_FISCAL_BASE_URL",
        "MOR_FISCAL_CLIENT_ID",
        "MOR_FISCAL_CLIENT_SECRET",
        "MOR_FISCAL_CLIENT_CERT_PATH",
        "MOR_FISCAL_CLIENT_KEY_PATH",
        "MOR_TAXPAYER_TIN",
    )
    blockers = (
        "authoritative MoR technical API specification",
        "taxpayer enrolment and production credentials",
        "digital certificate issued to the taxpayer",
        "official IRN and QR payload format",
        "official offline/store-and-forward rules",
    )


class AccreditedFiscalProvider(_UnimplementedFiscalProvider):
    """A commercial accredited fiscal service provider acting as intermediary."""

    name = "accredited"
    required_settings = (
        "FISCAL_PROVIDER_BASE_URL",
        "FISCAL_PROVIDER_API_KEY",
        "FISCAL_PROVIDER_DEVICE_ID",
    )
    blockers = (
        "signed contract with an accredited provider",
        "provider API documentation and sandbox access",
        "device/branch registration identifiers",
    )
