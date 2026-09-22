"""Provider selection and configuration.

The properties that matter for swapping a rail safely:

* mocks are the default and need no credentials;
* a named real provider is resolved from configuration alone;
* an unknown name is rejected with the valid options listed;
* a real provider without credentials fails loudly and NEVER silently falls
  back to the mock;
* swapping the provider does not change the normalized contract.
"""

from __future__ import annotations

import os

import pytest

from app.config import Settings, get_settings
from app.domain.errors import ProviderNotConfigured, ValidationError
from app.providers.registry import (
    DELIVERY_PROVIDERS,
    PAYMENT_PROVIDERS,
    describe_providers,
    get_delivery_provider,
    get_fiscal_provider,
    get_payment_provider,
    validate_provider_configuration,
)


def settings_with(**overrides) -> Settings:
    """A Settings object built directly, bypassing the process environment."""
    base = {
        "gateway_env": "test",
        "gateway_database_url": "sqlite+aiosqlite:///./unused.db",
        "fiscal_provider": "mock",
        "payment_provider": "mock",
        "delivery_provider": "mock",
    }
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
def test_mocks_are_the_default_on_every_rail():
    settings = settings_with()
    assert describe_providers(settings) == {
        "fiscal": "mock",
        "payment": "mock",
        "delivery": "mock",
    }


def test_mock_providers_need_no_configuration():
    # Must not raise: mocks are usable out of the box.
    validate_provider_configuration(settings_with())


def test_env_example_ships_mocks_as_the_default():
    """The committed example must not point at a half-configured real rail."""
    from pathlib import Path

    env_example = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
    assert "FISCAL_PROVIDER=mock" in env_example
    assert "PAYMENT_PROVIDER=mock" in env_example
    assert "DELIVERY_PROVIDER=mock" in env_example


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
async def test_configured_provider_is_resolved(session):
    settings = settings_with()
    assert get_fiscal_provider(settings, session).name == "mock"
    assert get_payment_provider(settings, session).name == "mock"
    assert get_delivery_provider(settings, session).name == "mock"


async def test_provider_can_be_overridden_per_request(session):
    """The X-Provider header selects a provider without touching config."""
    settings = settings_with()
    provider = get_fiscal_provider(settings, session, "mock")
    assert provider.name == "mock"


@pytest.mark.parametrize(
    "getter", [get_fiscal_provider, get_payment_provider, get_delivery_provider]
)
def test_unknown_provider_name_is_rejected_with_the_options(session, getter):
    with pytest.raises(ValidationError) as excinfo:
        getter(settings_with(), session, "does-not-exist")
    assert "available" in excinfo.value.detail


def test_unknown_provider_in_configuration_fails_startup():
    with pytest.raises(RuntimeError) as excinfo:
        validate_provider_configuration(settings_with(payment_provider="nope"))
    assert "not a known provider" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Fail fast - the important one
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("rail", "provider"),
    [
        ("payment_provider", "provider_a"),
        ("payment_provider", "provider_b"),
        ("delivery_provider", "provider_a"),
        ("fiscal_provider", "mor"),
        ("fiscal_provider", "accredited"),
    ],
)
def test_real_provider_without_credentials_fails_startup(rail, provider):
    with pytest.raises(RuntimeError) as excinfo:
        validate_provider_configuration(settings_with(**{rail: provider}))
    message = str(excinfo.value)
    assert provider in message
    assert "invalid provider configuration" in message


def test_failure_does_not_fall_back_to_mock():
    """The cardinal rule: a misconfigured real rail must never become a mock."""
    settings = settings_with(payment_provider="provider_a")
    with pytest.raises(RuntimeError):
        validate_provider_configuration(settings)
    # Selection is unchanged - nothing quietly rewrote it to "mock".
    assert settings.payment_provider == "provider_a"


def test_selecting_a_seam_provider_reports_what_is_missing(session):
    with pytest.raises(ProviderNotConfigured) as excinfo:
        PAYMENT_PROVIDERS["provider_a"](settings_with(), session)
    detail = excinfo.value.detail
    assert "PAYMENT_PROVIDER_A_API_KEY" in detail["required_settings"]
    assert detail["blockers"], "a seam must say what is blocking it"
    assert detail["documentation"] == "docs/providers.md"


def test_seam_provider_with_credentials_still_reports_unimplemented(session):
    """Credentials alone are not enough while the API document is missing."""
    configured = settings_with(
        payment_provider="provider_a",
        payment_provider_a_base_url="https://example.invalid",
        payment_provider_a_api_key="key",
        payment_provider_a_merchant_id="merchant",
    )
    with pytest.raises(ProviderNotConfigured) as excinfo:
        PAYMENT_PROVIDERS["provider_a"](configured, session)
    assert "not implemented" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Contract stability across providers
# ---------------------------------------------------------------------------
def test_every_registered_provider_implements_its_contract():
    from app.providers.delivery.base import DeliveryProvider
    from app.providers.payment.base import PaymentProvider

    for name, factory in PAYMENT_PROVIDERS.items():
        assert issubclass(factory, PaymentProvider), name
        assert factory.name == name, f"{name} registry key must match provider name"
    for name, factory in DELIVERY_PROVIDERS.items():
        assert issubclass(factory, DeliveryProvider), name
        assert factory.name == name, f"{name} registry key must match provider name"


def test_switching_provider_does_not_change_the_contract():
    """Odoo's payload is provider-agnostic; swapping a rail cannot alter it."""
    import inspect

    from app.providers.payment.base import PaymentProvider

    baseline = {
        name: inspect.signature(member)
        for name, member in inspect.getmembers(PaymentProvider, predicate=inspect.isfunction)
        if getattr(member, "__isabstractmethod__", False)
    }
    assert baseline, "the payment contract must declare abstract methods"

    for name, factory in PAYMENT_PROVIDERS.items():
        for method_name, signature in baseline.items():
            actual = inspect.signature(getattr(factory, method_name))
            assert actual == signature, f"{name}.{method_name} diverges from the contract"


async def test_active_provider_is_visible_without_exposing_secrets(client):
    """The health endpoint names providers but never leaks credentials."""
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["providers"] == {"fiscal": "mock", "payment": "mock", "delivery": "mock"}
    assert "api_key" not in response.text.lower()
    assert "secret" not in response.text.lower()


def test_settings_expose_rail_scoped_credentials():
    """Payment credentials must not be reachable as delivery credentials."""
    settings = settings_with(payment_provider_a_api_key="p-secret")
    assert settings.payment_provider_a_api_key == "p-secret"
    assert settings.delivery_provider_a_api_key == ""


@pytest.fixture(autouse=True)
def _restore_settings_cache():
    yield
    get_settings.cache_clear()
    os.environ.pop("PAYMENT_PROVIDER", None)
