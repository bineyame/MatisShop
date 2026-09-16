"""Payment rail: contract, idempotency, failure, refunds."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import func, select

from app.persistence.models import PaymentTransaction
from tests.conftest import payment_payload


async def test_create_payment_succeeds_in_auto_success_mode(client):
    response = await client.post("/api/v1/payments", json=payment_payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["provider"] == "mock"
    assert body["provider_transaction_id"].startswith("mp_")
    assert body["amount"] == "6900.00"
    assert body["checkout_url"]


async def test_payment_is_idempotent(client, session):
    payload = payment_payload()
    first = (await client.post("/api/v1/payments", json=payload)).json()
    second = (await client.post("/api/v1/payments", json=payload)).json()

    assert first["id"] == second["id"]
    assert first["provider_transaction_id"] == second["provider_transaction_id"]
    assert second["replayed"] is True

    count = await session.scalar(select(func.count()).select_from(PaymentTransaction))
    assert count == 1


async def test_payment_key_reuse_with_different_body_is_rejected(client):
    payload = payment_payload()
    await client.post("/api/v1/payments", json=payload)

    tampered = dict(payload, amount="1.00")
    response = await client.post("/api/v1/payments", json=tampered)
    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_conflict"


async def test_payment_can_be_looked_up_by_odoo_reference(client):
    payload = payment_payload()
    created = (await client.post("/api/v1/payments", json=payload)).json()

    response = await client.get(f"/api/v1/payments/by-reference/{payload['reference']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


async def test_unknown_reference_is_404(client):
    response = await client.get("/api/v1/payments/by-reference/NOPE")
    assert response.status_code == 404


async def test_verify_reconciles_against_the_provider(client):
    created = (await client.post("/api/v1/payments", json=payment_payload())).json()
    response = await client.post(f"/api/v1/payments/{created['id']}/verify")
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"


async def test_refund_moves_the_transaction_to_refunded(client):
    created = (await client.post("/api/v1/payments", json=payment_payload())).json()
    response = await client.post(
        f"/api/v1/payments/{created['id']}/refund",
        json={"idempotency_key": "refund-" + created["id"], "reason": "returned"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "refunded"
    assert body["refunded_amount"] == "6900.00"


async def test_partial_refund_then_full_refund(client):
    created = (await client.post("/api/v1/payments", json=payment_payload())).json()

    first = await client.post(
        f"/api/v1/payments/{created['id']}/refund",
        json={"idempotency_key": "r1-" + created["id"], "amount": "2000.00"},
    )
    assert first.json()["status"] == "partially_refunded"
    assert first.json()["refunded_amount"] == "2000.00"

    second = await client.post(
        f"/api/v1/payments/{created['id']}/refund",
        json={"idempotency_key": "r2-" + created["id"], "amount": "4900.00"},
    )
    assert second.json()["status"] == "refunded"
    assert second.json()["refunded_amount"] == "6900.00"


async def test_refund_beyond_the_paid_amount_is_rejected(client):
    created = (await client.post("/api/v1/payments", json=payment_payload())).json()
    response = await client.post(
        f"/api/v1/payments/{created['id']}/refund",
        json={"idempotency_key": "r-too-big", "amount": "99999.00"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_amount_must_be_positive(client):
    payload = payment_payload(amount="0.00")
    response = await client.post("/api/v1/payments", json=payload)
    assert response.status_code == 422


@pytest.fixture
def failing_payment_provider():
    from app.config import get_settings

    previous = os.environ.get("MOCK_PAYMENT_MODE")
    os.environ["MOCK_PAYMENT_MODE"] = "always_fail"
    get_settings.cache_clear()
    yield
    if previous is None:
        os.environ.pop("MOCK_PAYMENT_MODE", None)
    else:
        os.environ["MOCK_PAYMENT_MODE"] = previous
    get_settings.cache_clear()


async def test_provider_rejection_records_a_failed_transaction(client, failing_payment_provider):
    body = (await client.post("/api/v1/payments", json=payment_payload())).json()
    assert body["status"] == "failed"
    assert "rejected" in body["last_error"]


@pytest.fixture
def manual_payment_provider():
    from app.config import get_settings

    previous = os.environ.get("MOCK_PAYMENT_MODE")
    os.environ["MOCK_PAYMENT_MODE"] = "manual"
    get_settings.cache_clear()
    yield
    if previous is None:
        os.environ.pop("MOCK_PAYMENT_MODE", None)
    else:
        os.environ["MOCK_PAYMENT_MODE"] = previous
    get_settings.cache_clear()


async def test_manual_payment_stays_pending_until_authorized(client, manual_payment_provider):
    created = (await client.post("/api/v1/payments", json=payment_payload())).json()
    assert created["status"] == "pending"

    provider_txn = created["provider_transaction_id"]
    authorize = await client.post(f"/api/v1/admin/mock/payments/{provider_txn}/authorize")
    assert authorize.status_code == 200

    verified = await client.post(f"/api/v1/payments/{created['id']}/verify")
    assert verified.json()["status"] == "succeeded"
