"""Fiscal rail: registration, idempotency, failure, retry, audit.

The critical test in this file is
``test_same_idempotency_key_returns_same_registration``: same key in, same
IRN out, exactly one registration in the provider ledger.
"""

from __future__ import annotations

import base64
import uuid

import pytest
from sqlalchemy import func, select

from app.persistence.models import (
    FiscalDocument,
    IntegrationEvent,
    IntegrationRequest,
    MockFiscalRegistration,
)
from tests.conftest import fiscal_payload


async def set_failure_mode(client, enabled: bool) -> None:
    response = await client.post("/api/v1/admin/mock/fiscal/failure-mode", json={"enabled": enabled})
    assert response.status_code == 200, response.text
    assert response.json()["failure_mode"] is enabled


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
async def test_register_document_returns_irn_and_qr(client):
    response = await client.post("/api/v1/fiscal/documents", json=fiscal_payload())
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "registered"
    assert body["provider"] == "mock"
    assert body["irn"].startswith("ET-DEMO-")
    assert body["provider_transaction_id"].startswith("fp_")
    assert body["qr_payload"]
    assert body["attempt_count"] == 1
    assert body["replayed"] is False
    assert body["registered_at"] is not None
    assert body["last_error"] is None


async def test_qr_payload_is_decodable_and_contains_the_irn(client):
    response = await client.post("/api/v1/fiscal/documents", json=fiscal_payload())
    body = response.json()
    decoded = base64.b64decode(body["qr_payload"]).decode("utf-8")
    parts = decoded.split("|")
    assert parts[0] == "ETDEMO1"
    assert parts[1] == body["irn"]
    assert parts[-1] == "ETB"


async def test_money_is_returned_as_exact_decimal_strings(client):
    payload = fiscal_payload(subtotal="6199.99", tax_total="930.00", grand_total="7129.99")
    response = await client.post("/api/v1/fiscal/documents", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["grand_total"] == "7129.99"


async def test_irn_sequence_is_monotonic(client):
    first = (await client.post("/api/v1/fiscal/documents", json=fiscal_payload())).json()
    second = (await client.post("/api/v1/fiscal/documents", json=fiscal_payload())).json()
    assert first["irn"] != second["irn"]
    assert int(first["irn"].split("-")[-1]) + 1 == int(second["irn"].split("-")[-1])


async def test_document_can_be_read_back_by_id_and_by_key(client):
    payload = fiscal_payload()
    created = (await client.post("/api/v1/fiscal/documents", json=payload)).json()

    by_id = await client.get(f"/api/v1/fiscal/documents/{created['id']}")
    assert by_id.status_code == 200
    assert by_id.json()["irn"] == created["irn"]

    by_key = await client.get(f"/api/v1/fiscal/documents/by-key/{payload['idempotency_key']}")
    assert by_key.status_code == 200
    assert by_key.json()["id"] == created["id"]


async def test_unknown_document_is_404(client):
    response = await client.get("/api/v1/fiscal/documents/does-not-exist")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


# ---------------------------------------------------------------------------
# Idempotency - the critical property
# ---------------------------------------------------------------------------
async def test_same_idempotency_key_returns_same_registration(client, session):
    payload = fiscal_payload()

    first = (await client.post("/api/v1/fiscal/documents", json=payload)).json()
    second = (await client.post("/api/v1/fiscal/documents", json=payload)).json()
    third = (await client.post("/api/v1/fiscal/documents", json=payload)).json()

    assert first["irn"] == second["irn"] == third["irn"]
    assert first["id"] == second["id"] == third["id"]
    assert first["provider_transaction_id"] == second["provider_transaction_id"]
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert third["replayed"] is True

    # Exactly one registration exists at the provider and exactly one document
    # exists in the gateway.
    registrations = await session.scalar(select(func.count()).select_from(MockFiscalRegistration))
    documents = await session.scalar(select(func.count()).select_from(FiscalDocument))
    assert registrations == 1
    assert documents == 1


async def test_replay_does_not_call_the_provider_again(client, session):
    payload = fiscal_payload()
    await client.post("/api/v1/fiscal/documents", json=payload)
    await client.post("/api/v1/fiscal/documents", json=payload)

    attempts = await session.scalar(
        select(func.count()).select_from(IntegrationRequest).where(
            IntegrationRequest.operation == "register"
        )
    )
    assert attempts == 1


async def test_same_key_with_different_body_is_rejected(client):
    payload = fiscal_payload()
    await client.post("/api/v1/fiscal/documents", json=payload)

    tampered = fiscal_payload(idempotency_key=payload["idempotency_key"])
    tampered["totals"]["subtotal"] = "5000.00"
    tampered["totals"]["tax_total"] = "750.00"
    tampered["totals"]["grand_total"] = "5750.00"
    tampered["lines"][0]["line_total"] = "5000.00"
    tampered["lines"][0]["unit_price"] = "5000.00"

    response = await client.post("/api/v1/fiscal/documents", json=tampered)
    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_conflict"


async def test_idempotency_key_header_must_match_body(client):
    payload = fiscal_payload()
    response = await client.post(
        "/api/v1/fiscal/documents",
        json=payload,
        headers={"Idempotency-Key": "a-different-key"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_matching_idempotency_key_header_is_accepted(client):
    payload = fiscal_payload()
    response = await client.post(
        "/api/v1/fiscal/documents",
        json=payload,
        headers={"Idempotency-Key": payload["idempotency_key"]},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "registered"


# ---------------------------------------------------------------------------
# Failure and recovery
# ---------------------------------------------------------------------------
async def test_provider_failure_leaves_a_retryable_failed_document(client):
    await set_failure_mode(client, True)
    response = await client.post("/api/v1/fiscal/documents", json=fiscal_payload())

    # The gateway recorded the request successfully; the registration failed.
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["irn"] is None
    assert "failure mode" in body["last_error"]
    # Every attempt of the bounded retry loop was made.
    assert body["attempt_count"] == 3


async def test_failed_document_retries_to_exactly_one_registration(client, session):
    payload = fiscal_payload()

    await set_failure_mode(client, True)
    failed = (await client.post("/api/v1/fiscal/documents", json=payload)).json()
    assert failed["status"] == "failed"

    await set_failure_mode(client, False)
    retried = (await client.post(f"/api/v1/fiscal/documents/{failed['id']}/retry")).json()

    assert retried["status"] == "registered"
    assert retried["id"] == failed["id"]
    assert retried["irn"].startswith("ET-DEMO-")
    # 3 failed attempts + 1 successful one, all on the same document.
    assert retried["attempt_count"] == 4

    registrations = await session.scalar(select(func.count()).select_from(MockFiscalRegistration))
    assert registrations == 1


async def test_resubmitting_the_same_payload_after_failure_also_recovers(client, session):
    """Odoo may retry by re-POSTing rather than calling /retry. Same result."""
    payload = fiscal_payload()

    await set_failure_mode(client, True)
    failed = (await client.post("/api/v1/fiscal/documents", json=payload)).json()
    assert failed["status"] == "failed"

    await set_failure_mode(client, False)
    recovered = (await client.post("/api/v1/fiscal/documents", json=payload)).json()

    assert recovered["status"] == "registered"
    assert recovered["id"] == failed["id"]
    registrations = await session.scalar(select(func.count()).select_from(MockFiscalRegistration))
    assert registrations == 1


async def test_lost_response_cannot_create_a_second_irn(client, session):
    """The provider deduplicates too - the last line of defence.

    Simulates the nastiest failure: the provider registered the document, but
    the gateway crashed before committing, so it has no memory of it at all
    (no document row, no idempotency record). The provider ledger, however,
    kept the registration.

    Odoo then retries with the same idempotency key. The correct outcome is
    the ORIGINAL IRN and still exactly one registration.
    """
    from app.persistence.models import IdempotencyRecord

    payload = fiscal_payload()
    first = (await client.post("/api/v1/fiscal/documents", json=payload)).json()
    original_irn = first["irn"]

    document = await session.get(FiscalDocument, first["id"])
    await session.delete(document)
    record = (
        await session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == payload["idempotency_key"]
            )
        )
    ).scalars().one()
    await session.delete(record)
    await session.commit()

    retried = (await client.post("/api/v1/fiscal/documents", json=payload)).json()

    assert retried["status"] == "registered"
    assert retried["irn"] == original_irn
    assert retried["provider_transaction_id"] == first["provider_transaction_id"]

    registrations = await session.scalar(select(func.count()).select_from(MockFiscalRegistration))
    assert registrations == 1


async def test_retry_of_a_registered_document_is_a_no_op(client, session):
    registered = (await client.post("/api/v1/fiscal/documents", json=fiscal_payload())).json()
    again = (await client.post(f"/api/v1/fiscal/documents/{registered['id']}/retry")).json()

    assert again["irn"] == registered["irn"]
    assert again["replayed"] is True
    assert again["attempt_count"] == registered["attempt_count"]
    registrations = await session.scalar(select(func.count()).select_from(MockFiscalRegistration))
    assert registrations == 1


async def test_failure_mode_can_be_inspected(client):
    await set_failure_mode(client, True)
    response = await client.get("/api/v1/admin/mock/fiscal/failure-mode")
    assert response.json()["failure_mode"] is True


# ---------------------------------------------------------------------------
# Validation - malformed and dishonest payloads
# ---------------------------------------------------------------------------
async def test_totals_must_be_internally_consistent(client):
    payload = fiscal_payload(subtotal="6000.00", tax_total="900.00", grand_total="1.00")
    response = await client.post("/api/v1/fiscal/documents", json=payload)
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_line_totals_must_match_the_subtotal(client):
    payload = fiscal_payload()
    payload["lines"][0]["line_total"] = "10.00"
    response = await client.post("/api/v1/fiscal/documents", json=payload)
    assert response.status_code == 422


async def test_at_least_one_line_is_required(client):
    payload = fiscal_payload()
    payload["lines"] = []
    response = await client.post("/api/v1/fiscal/documents", json=payload)
    assert response.status_code == 422


async def test_unknown_fields_are_rejected(client):
    payload = fiscal_payload()
    payload["surprise_field"] = "nope"
    response = await client.post("/api/v1/fiscal/documents", json=payload)
    assert response.status_code == 422


async def test_provider_rejection_is_a_permanent_failure(client, session):
    """No seller TIN: the mock rejects outright rather than asking for a retry."""
    payload = fiscal_payload(seller_tin=None)
    body = (await client.post("/api/v1/fiscal/documents", json=payload)).json()

    assert body["status"] == "failed"
    assert "TIN" in body["last_error"]
    # Permanent errors are not retried: exactly one attempt.
    assert body["attempt_count"] == 1

    outcomes = (
        (
            await session.execute(
                select(IntegrationRequest.outcome).where(IntegrationRequest.operation == "register")
            )
        )
        .scalars()
        .all()
    )
    assert outcomes == ["permanent_error"]


# ---------------------------------------------------------------------------
# Audit and traceability
# ---------------------------------------------------------------------------
async def test_every_attempt_and_transition_is_audited(client, session):
    payload = fiscal_payload()
    await set_failure_mode(client, True)
    failed = (await client.post("/api/v1/fiscal/documents", json=payload)).json()
    await set_failure_mode(client, False)
    await client.post(f"/api/v1/fiscal/documents/{failed['id']}/retry")

    attempts = (
        (
            await session.execute(
                select(IntegrationRequest)
                .where(IntegrationRequest.resource_id == failed["id"])
                .order_by(IntegrationRequest.created_at)
            )
        )
        .scalars()
        .all()
    )
    assert [row.outcome for row in attempts] == [
        "transient_error",
        "transient_error",
        "transient_error",
        "success",
    ]
    # Failure history is preserved, not overwritten by the later success.
    assert attempts[0].error is not None
    assert attempts[-1].error is None

    events = (
        (
            await session.execute(
                select(IntegrationEvent)
                .where(IntegrationEvent.resource_id == failed["id"])
                .order_by(IntegrationEvent.created_at)
            )
        )
        .scalars()
        .all()
    )
    assert [row.event_type for row in events] == [
        "created",
        "submitting",
        "failed",
        "submitting",
        "registered",
    ]


async def test_audit_endpoint_answers_the_forensic_questions(client):
    payload = fiscal_payload(record_id="4242")
    created = (await client.post("/api/v1/fiscal/documents", json=payload)).json()

    response = await client.get(f"/api/v1/admin/audit/fiscal_document/{created['id']}")
    assert response.status_code == 200
    audit = response.json()

    assert audit["attempt_count"] == 1
    attempt = audit["attempts"][0]
    assert attempt["provider"] == "mock"
    assert attempt["outcome"] == "success"
    assert attempt["request_payload"]["source"]["record_id"] == "4242"
    assert attempt["response_payload"]["irn"] == created["irn"]
    assert [t["to_status"] for t in audit["transitions"]] == ["pending", "submitting", "registered"]


async def test_correlation_id_is_echoed_and_stored(client, session):
    correlation = uuid.uuid4().hex
    response = await client.post(
        "/api/v1/fiscal/documents",
        json=fiscal_payload(),
        headers={"X-Correlation-Id": correlation},
    )
    assert response.headers["X-Correlation-Id"] == correlation
    assert response.json()["correlation_id"] == correlation

    stored = await session.scalar(
        select(IntegrationRequest.correlation_id).where(
            IntegrationRequest.resource_id == response.json()["id"]
        )
    )
    assert stored == correlation


async def test_source_record_linkage_is_preserved(client, session):
    payload = fiscal_payload(record_id="777", document_number="Shop 1 Retail/0099")
    created = (await client.post("/api/v1/fiscal/documents", json=payload)).json()

    document = await session.get(FiscalDocument, created["id"])
    assert document.source_system == "odoo"
    assert document.source_model == "pos.order"
    assert document.source_record_id == "777"
    assert document.external_reference == "Shop 1 Retail/0099"
    assert document.document_number == "Shop 1 Retail/0099"


# ---------------------------------------------------------------------------
# Cancellation / state machine
# ---------------------------------------------------------------------------
async def test_registered_document_can_be_cancelled(client):
    created = (await client.post("/api/v1/fiscal/documents", json=fiscal_payload())).json()
    response = await client.post(
        f"/api/v1/fiscal/documents/{created['id']}/cancel",
        json={"idempotency_key": uuid.uuid4().hex, "reason": "customer returned the shoes"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


async def test_cancelled_document_cannot_be_cancelled_twice(client):
    created = (await client.post("/api/v1/fiscal/documents", json=fiscal_payload())).json()
    body = {"idempotency_key": uuid.uuid4().hex, "reason": "return"}
    await client.post(f"/api/v1/fiscal/documents/{created['id']}/cancel", json=body)

    second = await client.post(
        f"/api/v1/fiscal/documents/{created['id']}/cancel",
        json={"idempotency_key": uuid.uuid4().hex, "reason": "return again"},
    )
    assert second.status_code == 409
    assert second.json()["code"] == "invalid_state_transition"


async def test_cancelled_document_cannot_be_retried(client):
    created = (await client.post("/api/v1/fiscal/documents", json=fiscal_payload())).json()
    await client.post(
        f"/api/v1/fiscal/documents/{created['id']}/cancel",
        json={"idempotency_key": uuid.uuid4().hex, "reason": "return"},
    )
    response = await client.post(f"/api/v1/fiscal/documents/{created['id']}/retry")
    assert response.status_code in (409, 422)


@pytest.mark.parametrize(
    ("current", "target", "allowed"),
    [
        ("pending", "submitting", True),
        ("pending", "registered", False),
        ("submitting", "registered", True),
        ("submitting", "failed", True),
        ("failed", "submitting", True),
        ("registered", "pending", False),
        ("registered", "submitting", False),
        ("registered", "cancelled", True),
        ("cancelled", "submitting", False),
    ],
)
def test_fiscal_state_machine_rejects_invalid_transitions(current, target, allowed):
    from app.domain.state_machine import can_transition

    assert can_transition("fiscal", current, target) is allowed
