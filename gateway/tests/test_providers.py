"""Provider contracts and registry behaviour.

Two things matter here: every bundled provider satisfies its abstract
contract, and every real-provider extension point fails loudly and
informatively instead of pretending to work.
"""

from __future__ import annotations

import inspect

import pytest

from app.config import get_settings
from app.domain.errors import ProviderNotConfigured, ValidationError
from app.providers.delivery.base import DeliveryProvider
from app.providers.delivery.mock import MockDeliveryProvider
from app.providers.delivery.provider_a import ProviderADeliveryProvider
from app.providers.fiscal.base import FiscalProvider
from app.providers.fiscal.mock import MockFiscalProvider
from app.providers.fiscal.placeholders import AccreditedFiscalProvider, MoRFiscalProvider
from app.providers.payment.base import PaymentProvider
from app.providers.payment.mock import MockPaymentProvider
from app.providers.payment.provider_a import ProviderAPaymentProvider
from app.providers.payment.provider_b import ProviderBPaymentProvider
from app.providers.registry import (
    get_delivery_provider,
    get_fiscal_provider,
    get_payment_provider,
)
from tests.conftest import delivery_payload, fiscal_payload, payment_payload


@pytest.mark.parametrize(
    ("base", "implementation"),
    [
        (FiscalProvider, MockFiscalProvider),
        (FiscalProvider, MoRFiscalProvider),
        (FiscalProvider, AccreditedFiscalProvider),
        (PaymentProvider, MockPaymentProvider),
        (PaymentProvider, ProviderAPaymentProvider),
        (PaymentProvider, ProviderBPaymentProvider),
        (DeliveryProvider, MockDeliveryProvider),
        (DeliveryProvider, ProviderADeliveryProvider),
    ],
)
def test_provider_implements_its_contract(base, implementation):
    assert issubclass(implementation, base)
    assert not inspect.isabstract(implementation)
    assert implementation.name != "abstract"

    for name, member in inspect.getmembers(base, predicate=inspect.isfunction):
        if getattr(member, "__isabstractmethod__", False):
            assert inspect.iscoroutinefunction(getattr(implementation, name))


@pytest.mark.parametrize(
    "implementation",
    [
        MoRFiscalProvider,
        AccreditedFiscalProvider,
        ProviderAPaymentProvider,
        ProviderBPaymentProvider,
        ProviderADeliveryProvider,
    ],
)
def test_real_provider_placeholders_declare_what_they_need(implementation):
    assert implementation.required_settings, "placeholder must document its settings"
    assert implementation.blockers, "placeholder must document what is still missing"


def test_unimplemented_fiscal_provider_fails_on_selection(session):
    """Selecting it is enough to fail - you do not have to wait for a sale."""
    with pytest.raises(ProviderNotConfigured) as excinfo:
        MoRFiscalProvider(get_settings(), session)
    assert "MOR_FISCAL_BASE_URL" in str(excinfo.value.detail["required_settings"])


def test_unimplemented_payment_provider_fails_on_selection(session):
    with pytest.raises(ProviderNotConfigured):
        ProviderAPaymentProvider(get_settings(), session)


def test_unimplemented_delivery_provider_fails_on_selection(session):
    with pytest.raises(ProviderNotConfigured):
        ProviderADeliveryProvider(get_settings(), session)


async def test_selecting_a_seam_provider_surfaces_a_clear_http_error(client):
    """A misconfigured deployment must fail loudly, not silently mock things.

    The request is refused outright (501) rather than recorded as a merely
    "failed" document: the problem is the deployment, not the document, and
    retrying it would be pointless.
    """
    response = await client.post(
        "/api/v1/fiscal/documents", json=fiscal_payload(), headers={"X-Provider": "mor"}
    )
    assert response.status_code == 501
    body = response.json()
    assert body["code"] == "provider_not_configured"
    assert "not implemented" in body["message"]
    assert body["detail"]["blockers"]


def test_unknown_provider_name_is_a_validation_error(session):
    with pytest.raises(ValidationError):
        get_fiscal_provider(get_settings(), session, "nonexistent")
    with pytest.raises(ValidationError):
        get_payment_provider(get_settings(), session, "nonexistent")
    with pytest.raises(ValidationError):
        get_delivery_provider(get_settings(), session, "nonexistent")


def test_registry_default_is_the_configured_provider(session):
    settings = get_settings()
    assert get_fiscal_provider(settings, session).name == settings.fiscal_provider
    assert get_payment_provider(settings, session).name == settings.payment_provider
    assert get_delivery_provider(settings, session).name == settings.delivery_provider


async def test_mock_fiscal_provider_is_idempotent_at_the_provider_level(session):
    """Below the gateway's own idempotency layer, the provider dedupes too."""
    provider = MockFiscalProvider(get_settings(), session)
    request = _fiscal_request()

    first = await provider.register(request)
    second = await provider.register(request)

    assert first.irn == second.irn
    assert first.provider_transaction_id == second.provider_transaction_id
    assert second.raw_response["replayed"] is True


async def test_mock_fiscal_provider_marks_output_as_a_demo(session):
    provider = MockFiscalProvider(get_settings(), session)
    result = await provider.register(_fiscal_request())
    assert "NOT PRODUCTION CERTIFICATION" in result.raw_response["disclaimer"]


def _fiscal_request():
    from app.domain.contracts import FiscalDocumentRequest

    return FiscalDocumentRequest.model_validate(fiscal_payload())


def _payment_request():
    from app.domain.contracts import PaymentRequest

    return PaymentRequest.model_validate(payment_payload())


def _delivery_request():
    from app.domain.contracts import DeliveryRequest

    return DeliveryRequest.model_validate(delivery_payload())
