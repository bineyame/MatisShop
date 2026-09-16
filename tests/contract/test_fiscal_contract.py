"""Cross-boundary contract tests.

These sit between the two test suites:

* `gateway/tests`      the gateway's own behaviour
* `addons/*/tests`     Odoo's own behaviour, with the gateway stubbed
* here                 the payload Odoo produces really is what the gateway
                       accepts

The example documents in `docs/contracts/` are the shared reference. Odoo's
tests assert its generated payload has this shape; these tests assert the
gateway accepts exactly this shape and behaves correctly on it.

Run locally (no Docker needed):

    cd gateway && python -m pytest ../tests/contract -q

Run against a RUNNING gateway as well:

    GATEWAY_URL=http://localhost:8000 GATEWAY_API_KEY=... \\
        python -m pytest ../tests/contract -q
"""

from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "contracts"


def load_example(name: str) -> dict:
    payload = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
    payload.pop("_comment", None)
    return payload


@pytest.fixture
def fiscal_example() -> dict:
    payload = load_example("fiscal_document.example.json")
    payload["idempotency_key"] = uuid.uuid4().hex
    return payload


@pytest.fixture
def payment_example() -> dict:
    payload = load_example("payment_request.example.json")
    payload["idempotency_key"] = uuid.uuid4().hex
    return payload


@pytest.fixture
def delivery_example() -> dict:
    payload = load_example("delivery_request.example.json")
    payload["idempotency_key"] = uuid.uuid4().hex
    return payload


# ---------------------------------------------------------------------------
# Schema-level: the example is valid against the gateway's own contract
# ---------------------------------------------------------------------------
def test_fiscal_example_validates_against_the_contract(fiscal_example):
    from app.domain.contracts import FiscalDocumentRequest

    request = FiscalDocumentRequest.model_validate(fiscal_example)

    assert request.source.system == "odoo"
    assert request.source.model == "pos.order"
    assert request.seller.tin, "the seller TIN is mandatory for fiscalization"
    assert request.lines[0].sku == "SAM-BLK-42"
    assert request.totals.grand_total == Decimal("6900.00")


def test_payment_example_validates_against_the_contract(payment_example):
    from app.domain.contracts import PaymentRequest

    request = PaymentRequest.model_validate(payment_example)
    assert request.amount > 0
    assert request.currency == "ETB"


def test_delivery_example_validates_against_the_contract(delivery_example):
    from app.domain.contracts import DeliveryRequest

    request = DeliveryRequest.model_validate(delivery_example)
    assert request.packages
    assert request.pickup.name
    assert request.dropoff.name


def test_money_is_exchanged_as_strings_not_floats(fiscal_example):
    """A fiscal total that is off by a cent is a rejected document.

    JSON floats cannot represent 6199.99 exactly, so both sides use decimal
    strings. This guards the convention from drifting.
    """
    for key in ("subtotal", "tax_total", "grand_total"):
        assert isinstance(fiscal_example["totals"][key], str)

    from app.domain.contracts import FiscalDocumentRequest

    request = FiscalDocumentRequest.model_validate(fiscal_example)
    dumped = request.model_dump(mode="json")
    assert dumped["totals"]["grand_total"] == "6900.00"
    assert isinstance(dumped["totals"]["grand_total"], str)


def test_barcode_in_the_example_is_a_valid_ean13(fiscal_example):
    barcode = fiscal_example["lines"][0]["barcode"]
    assert len(barcode) == 13 and barcode.isdigit()
    total = sum(int(digit) * (3 if index % 2 else 1) for index, digit in enumerate(barcode[:12]))
    assert int(barcode[12]) == (10 - total % 10) % 10, "demo barcodes must be scannable"


# ---------------------------------------------------------------------------
# Behaviour-level: the gateway really processes the Odoo payload
# ---------------------------------------------------------------------------
async def test_gateway_registers_the_odoo_payload(client, fiscal_example):
    response = await client.post("/api/v1/fiscal/documents", json=fiscal_example)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["status"] == "registered"
    assert body["irn"]
    assert body["qr_payload"]
    assert body["grand_total"] == "6900.00"


async def test_resubmitting_the_odoo_payload_returns_the_same_irn(client, fiscal_example):
    first = (await client.post("/api/v1/fiscal/documents", json=fiscal_example)).json()
    second = (await client.post("/api/v1/fiscal/documents", json=fiscal_example)).json()

    assert first["irn"] == second["irn"]
    assert second["replayed"] is True


async def test_gateway_creates_the_payment_from_the_odoo_payload(client, payment_example):
    response = await client.post("/api/v1/payments", json=payment_example)
    assert response.status_code == 200, response.text
    assert response.json()["status"] in ("succeeded", "pending")


async def test_gateway_books_the_delivery_from_the_odoo_payload(client, delivery_example):
    response = await client.post("/api/v1/deliveries", json=delivery_example)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "created"


# ---------------------------------------------------------------------------
# Live gateway (opt-in): proves the same against the real running service
# ---------------------------------------------------------------------------
LIVE_URL = os.environ.get("GATEWAY_URL")


@pytest.mark.skipif(not LIVE_URL, reason="set GATEWAY_URL to test a running gateway")
def test_live_gateway_accepts_the_odoo_payload(fiscal_example):
    import httpx

    api_key = os.environ.get("GATEWAY_API_KEY", "dev-gateway-api-key-change-me")
    with httpx.Client(base_url=LIVE_URL, timeout=30.0) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "pass"

        first = client.post(
            "/api/v1/fiscal/documents", json=fiscal_example, headers={"X-API-Key": api_key}
        )
        assert first.status_code == 200, first.text
        second = client.post(
            "/api/v1/fiscal/documents", json=fiscal_example, headers={"X-API-Key": api_key}
        )
        assert first.json()["irn"] == second.json()["irn"]
