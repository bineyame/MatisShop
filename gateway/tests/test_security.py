"""Authentication, webhook signature verification and log/audit redaction."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from sqlalchemy import select

from app.observability import redact
from app.persistence.models import IntegrationRequest, WebhookEvent
from tests.conftest import WEBHOOK_SECRET, delivery_payload, fiscal_payload, payment_payload


def sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# API key
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/v1/fiscal/documents"),
        ("post", "/api/v1/payments"),
        ("post", "/api/v1/deliveries"),
        ("get", "/api/v1/admin/mock/fiscal/failure-mode"),
    ],
)
async def test_api_requires_an_api_key(anon_client, method, path):
    response = await anon_client.request(method.upper(), path, json={})
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_error"


async def test_wrong_api_key_is_rejected(anon_client):
    response = await anon_client.post(
        "/api/v1/fiscal/documents",
        json=fiscal_payload(),
        headers={"X-API-Key": "definitely-not-the-key"},
    )
    assert response.status_code == 401


async def test_health_is_public(anon_client):
    response = await anon_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "pass"


# ---------------------------------------------------------------------------
# Webhook signatures
# ---------------------------------------------------------------------------
async def test_unsigned_webhook_is_rejected_but_recorded(client, session):
    body = json.dumps({"event_id": "evt_1", "provider_transaction_id": "mp_x", "status": "succeeded"})
    response = await client.post(
        "/api/v1/webhooks/payment/mock", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 401

    event = (await session.execute(select(WebhookEvent))).scalars().one()
    assert event.signature_valid is False
    assert event.processed is False
    assert event.error == "invalid signature"
    # The rejected body is not retained.
    assert event.payload == {"_rejected": True}


async def test_badly_signed_webhook_is_rejected(client):
    body = json.dumps({"event_id": "evt_2", "provider_transaction_id": "mp_x", "status": "succeeded"})
    response = await client.post(
        "/api/v1/webhooks/payment/mock",
        content=body,
        headers={"X-Signature": sign(body.encode(), "the-wrong-secret")},
    )
    assert response.status_code == 401


async def test_correctly_signed_payment_webhook_updates_the_transaction(client, session):
    created = (await client.post("/api/v1/payments", json=payment_payload())).json()
    body = json.dumps(
        {
            "event_id": "evt_paid_1",
            "provider_transaction_id": created["provider_transaction_id"],
            "status": "succeeded",
        }
    )
    response = await client.post(
        "/api/v1/webhooks/payment/mock",
        content=body,
        headers={"X-Signature": sign(body.encode()), "Content-Type": "application/json"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "processed"
    assert response.json()["resource_id"] == created["id"]

    event = (
        (await session.execute(select(WebhookEvent).where(WebhookEvent.external_event_id == "evt_paid_1")))
        .scalars()
        .one()
    )
    assert event.signature_valid is True
    assert event.processed is True


async def test_duplicate_webhook_delivery_is_ignored(client):
    created = (await client.post("/api/v1/payments", json=payment_payload())).json()
    body = json.dumps(
        {
            "event_id": "evt_dup",
            "provider_transaction_id": created["provider_transaction_id"],
            "status": "succeeded",
        }
    )
    headers = {"X-Signature": sign(body.encode()), "Content-Type": "application/json"}

    first = await client.post("/api/v1/webhooks/payment/mock", content=body, headers=headers)
    second = await client.post("/api/v1/webhooks/payment/mock", content=body, headers=headers)

    assert first.json()["status"] == "processed"
    assert second.json()["status"] == "duplicate"


async def test_delivery_webhook_advances_the_order(client):
    created = (await client.post("/api/v1/deliveries", json=delivery_payload())).json()
    body = json.dumps(
        {
            "event_id": "evt_del_1",
            "provider_transaction_id": created["provider_transaction_id"],
            "status": "assigned",
        }
    )
    response = await client.post(
        "/api/v1/webhooks/delivery/mock",
        content=body,
        headers={"X-Signature": sign(body.encode()), "Content-Type": "application/json"},
    )
    assert response.json()["resource_status"] == "assigned"


async def test_webhook_for_an_unknown_resource_is_404(client):
    body = json.dumps(
        {"event_id": "evt_ghost", "provider_transaction_id": "mp_ghost", "status": "succeeded"}
    )
    response = await client.post(
        "/api/v1/webhooks/payment/mock",
        content=body,
        headers={"X-Signature": sign(body.encode()), "Content-Type": "application/json"},
    )
    assert response.status_code == 404


async def test_webhook_on_an_unsupported_rail_is_rejected(client):
    body = json.dumps({"event_id": "evt_x", "provider_transaction_id": "x", "status": "y"})
    response = await client.post(
        "/api/v1/webhooks/teleportation/mock",
        content=body,
        headers={"X-Signature": sign(body.encode())},
    )
    assert response.status_code == 422


async def test_malformed_webhook_body_does_not_crash(client):
    body = b"{not json at all"
    response = await client.post(
        "/api/v1/webhooks/payment/mock", content=body, headers={"X-Signature": sign(body)}
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Secret hygiene
# ---------------------------------------------------------------------------
def test_redaction_masks_sensitive_keys():
    payload = {
        "api_key": "super-secret",
        "nested": {"authorization": "Bearer abc", "safe": "keep me"},
        "list": [{"private_key": "-----BEGIN"}],
        "amount": "100.00",
    }
    cleaned = redact(payload)

    assert cleaned["api_key"] == "***REDACTED***"
    assert cleaned["nested"]["authorization"] == "***REDACTED***"
    assert cleaned["nested"]["safe"] == "keep me"
    assert cleaned["list"][0]["private_key"] == "***REDACTED***"
    assert cleaned["amount"] == "100.00"


def test_redaction_truncates_huge_strings():
    cleaned = redact({"blob": "x" * 10_000})
    assert cleaned["blob"].endswith("...[truncated]")
    assert len(cleaned["blob"]) < 5_000


async def test_audit_payloads_are_redacted(client, session):
    payload = payment_payload()
    payload["metadata"] = {"api_key": "leak-me-if-you-can", "channel": "website"}
    await client.post("/api/v1/payments", json=payload)

    row = (
        (
            await session.execute(
                select(IntegrationRequest).where(IntegrationRequest.operation == "create_payment")
            )
        )
        .scalars()
        .one()
    )
    assert row.request_payload["metadata"]["api_key"] == "***REDACTED***"
    assert row.request_payload["metadata"]["channel"] == "website"


async def test_validation_errors_do_not_echo_the_submitted_body(client):
    payload = fiscal_payload()
    payload["buyer"] = {"name": "Customer", "tin": "SENSITIVE-TIN", "unexpected": "x"}
    response = await client.post("/api/v1/fiscal/documents", json=payload)

    assert response.status_code == 422
    assert "SENSITIVE-TIN" not in response.text
