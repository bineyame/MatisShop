"""Extension points for REAL Ethiopian payment rails.

Not implemented on purpose. We do not have authoritative, current API
specifications for these providers, and a plausible-looking fake would be
worse than an explicit gap: it would pass review and fail in production.

A real adapter implements PaymentProvider and nothing else changes - not the
gateway API, not the Odoo addon. Switch PAYMENT_PROVIDER and restart.

See docs/integration-contracts.md#adding-a-real-payment-provider.
"""

from __future__ import annotations

from decimal import Decimal

from app.config import Settings
from app.domain.contracts import PaymentRequest
from app.domain.errors import ProviderNotConfigured
from app.providers.payment.base import (
    PaymentCreationResult,
    PaymentProvider,
    PaymentStatusResult,
    RefundResult,
)


class _UnimplementedPaymentProvider(PaymentProvider):
    required_settings: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    #: how the provider signs its callbacks - the webhook verifier needs this
    webhook_signature_scheme: str = "unknown"

    def __init__(self, settings: Settings, session=None) -> None:  # noqa: ANN001 - uniform factory
        self.settings = settings
        self.session = session

    def _unavailable(self) -> ProviderNotConfigured:
        return ProviderNotConfigured(
            f"payment provider '{self.name}' is an extension point and is not implemented",
            provider=self.name,
            detail={
                "required_settings": list(self.required_settings),
                "blockers": list(self.blockers),
                "webhook_signature_scheme": self.webhook_signature_scheme,
                "documentation": "docs/integration-contracts.md#adding-a-real-payment-provider",
            },
        )

    async def create_payment(self, request: PaymentRequest) -> PaymentCreationResult:
        raise self._unavailable()

    async def verify_payment(self, reference: str) -> PaymentStatusResult:
        raise self._unavailable()

    async def refund(self, reference: str, amount: Decimal | None = None) -> RefundResult:
        raise self._unavailable()


class ArifPayProvider(_UnimplementedPaymentProvider):
    name = "arifpay"
    required_settings = ("ARIFPAY_BASE_URL", "ARIFPAY_API_KEY", "ARIFPAY_WEBHOOK_SECRET")
    blockers = (
        "merchant account and production API key",
        "current ArifPay API reference and sandbox",
        "confirmed webhook signature scheme",
        "settlement/reconciliation rules",
    )


class ChapaProvider(_UnimplementedPaymentProvider):
    name = "chapa"
    required_settings = ("CHAPA_BASE_URL", "CHAPA_SECRET_KEY", "CHAPA_WEBHOOK_SECRET")
    blockers = (
        "merchant account and live secret key",
        "current Chapa API reference and test keys",
        "confirmed webhook signature scheme",
    )


class TelebirrProvider(_UnimplementedPaymentProvider):
    name = "telebirr"
    required_settings = (
        "TELEBIRR_BASE_URL",
        "TELEBIRR_APP_ID",
        "TELEBIRR_APP_KEY",
        "TELEBIRR_SHORT_CODE",
        "TELEBIRR_PUBLIC_KEY_PATH",
        "TELEBIRR_PRIVATE_KEY_PATH",
    )
    blockers = (
        "merchant onboarding with Ethio Telecom",
        "RSA key pair exchange",
        "current Telebirr integration specification",
        "confirmed notification/callback verification rules",
    )
