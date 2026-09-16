"""Gateway persistence model.

Four groups of tables:

1. Idempotency      - one logical external operation happens at most once.
2. Rail resources   - fiscal documents, payment transactions, delivery orders.
3. Audit            - every provider attempt and every status transition.
4. Mock provider    - state belonging to the bundled demo providers only.

Nothing here is an Odoo table and no Odoo table is ever read.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.database import Base


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# 1. Idempotency
# ---------------------------------------------------------------------------
class IdempotencyRecord(Base, TimestampMixin):
    """One row per (scope, idempotency key).

    The unique constraint is the actual enforcement point: two concurrent
    identical requests race on the database, not on application logic.
    """

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("scope", "idempotency_key", name="uq_idempotency_scope_key"),
        Index("ix_idempotency_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    # sha256 of the canonicalised request body - re-using a key with a different
    # body is a client bug and must be rejected, not silently served.
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # in_progress | completed | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="in_progress")
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[str | None] = mapped_column(String(32))
    response_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    correlation_id: Mapped[str | None] = mapped_column(String(64))


# ---------------------------------------------------------------------------
# 2. Rail resources
# ---------------------------------------------------------------------------
class FiscalDocument(Base, TimestampMixin):
    """A fiscal registration request and its outcome.

    Mirrors et.fiscal.transaction on the Odoo side, but the two records are
    independent and linked only by the idempotency key and the source
    reference. Neither system writes into the other database.
    """

    __tablename__ = "fiscal_documents"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_fiscal_idempotency_key"),
        Index("ix_fiscal_source", "source_model", "source_record_id"),
        Index("ix_fiscal_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64))

    # Where this came from: answers "which ERP transaction triggered this?"
    source_system: Mapped[str] = mapped_column(String(32), nullable=False, default="odoo")
    source_model: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(128))

    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    document_number: Mapped[str | None] = mapped_column(String(128))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # pending | submitting | registered | failed | cancelled
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="ETB")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=Decimal("0"))
    grand_total: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=Decimal("0"))

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_transaction_id: Mapped[str | None] = mapped_column(String(128))
    irn: Mapped[str | None] = mapped_column(String(128))
    qr_payload: Mapped[str | None] = mapped_column(Text)

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)

    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    provider_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentTransaction(Base, TimestampMixin):
    __tablename__ = "payment_transactions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_payment_idempotency_key"),
        Index("ix_payment_reference", "reference"),
        Index("ix_payment_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64))

    # ERP-side reference, e.g. the Odoo payment.transaction reference.
    reference: Mapped[str] = mapped_column(String(128), nullable=False)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False, default="odoo")
    source_model: Mapped[str | None] = mapped_column(String(64))
    source_record_id: Mapped[str | None] = mapped_column(String(64))

    # pending | authorized | succeeded | failed | cancelled | refunded | partially_refunded
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="ETB")
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=Decimal("0"))
    refunded_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=Decimal("0"))

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_transaction_id: Mapped[str | None] = mapped_column(String(128))
    checkout_url: Mapped[str | None] = mapped_column(Text)

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    provider_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeliveryOrder(Base, TimestampMixin):
    __tablename__ = "delivery_orders"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_delivery_idempotency_key"),
        Index("ix_delivery_reference", "reference"),
        Index("ix_delivery_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64))

    reference: Mapped[str] = mapped_column(String(128), nullable=False)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False, default="odoo")
    source_model: Mapped[str | None] = mapped_column(String(64))
    source_record_id: Mapped[str | None] = mapped_column(String(64))

    # created | assigned | picked_up | in_transit | delivered | failed | cancelled
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="created")
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_transaction_id: Mapped[str | None] = mapped_column(String(128))
    tracking_number: Mapped[str | None] = mapped_column(String(128))
    tracking_url: Mapped[str | None] = mapped_column(Text)

    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="ETB")
    price: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=Decimal("0"))

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    provider_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# 3. Audit
# ---------------------------------------------------------------------------
class IntegrationRequest(Base):
    """One row per provider call attempt. Never updated, never deleted.

    This is what makes "how many attempts, and what came back each time?"
    answerable months later.
    """

    __tablename__ = "integration_requests"
    __table_args__ = (
        Index("ix_request_resource", "resource_type", "resource_id"),
        Index("ix_request_idempotency", "idempotency_key"),
        Index("ix_request_correlation", "correlation_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    rail: Mapped[str] = mapped_column(String(16), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[str | None] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # success | transient_error | permanent_error
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class IntegrationEvent(Base):
    """Append-only status transition log for any rail resource."""

    __tablename__ = "integration_events"
    __table_args__ = (Index("ix_event_resource", "resource_type", "resource_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    rail: Mapped[str] = mapped_column(String(16), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str | None] = mapped_column(String(24))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class WebhookEvent(Base):
    """Every inbound provider callback, valid or not.

    Rejected callbacks are stored too: an attacker probing the endpoint is
    exactly the thing you want a record of.
    """

    __tablename__ = "webhook_events"
    __table_args__ = (
        Index("ix_webhook_rail_provider", "rail", "provider"),
        UniqueConstraint("provider", "external_event_id", name="uq_webhook_provider_event"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    rail: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[str | None] = mapped_column(String(32))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# 4. Mock provider state (demo providers only)
# ---------------------------------------------------------------------------
class MockFiscalRegistration(Base):
    """The mock fiscal provider own ledger.

    A real accredited provider deduplicates on its side too; modelling that
    here is what proves a lost response cannot create a second IRN.
    """

    __tablename__ = "mock_fiscal_registrations"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_mock_fiscal_idempotency"),)

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_transaction_id: Mapped[str] = mapped_column(String(128), nullable=False)
    irn: Mapped[str] = mapped_column(String(128), nullable=False)
    qr_payload: Mapped[str] = mapped_column(Text, nullable=False)
    document_number: Mapped[str | None] = mapped_column(String(128))
    grand_total: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="registered")
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MockProviderState(Base):
    """Runtime switches for the demo providers (e.g. forced failure mode).

    Stored in the database rather than a module global so the toggle is shared
    by every worker process and survives a gateway restart.
    """

    __tablename__ = "mock_provider_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
