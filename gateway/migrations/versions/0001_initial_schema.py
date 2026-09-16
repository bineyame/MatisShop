"""initial gateway schema

Revision ID: 0001
Revises:
Create Date: 2026-01-01

Creates the gateway's own tables. None of these live in the Odoo database.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="in_progress"),
        sa.Column("resource_type", sa.String(32)),
        sa.Column("resource_id", sa.String(32)),
        sa.Column("response_snapshot", sa.JSON()),
        sa.Column("correlation_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scope", "idempotency_key", name="uq_idempotency_scope_key"),
    )
    op.create_index("ix_idempotency_status", "idempotency_records", ["status"])

    op.create_table(
        "fiscal_documents",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("correlation_id", sa.String(64)),
        sa.Column("source_system", sa.String(32), nullable=False, server_default="odoo"),
        sa.Column("source_model", sa.String(64), nullable=False),
        sa.Column("source_record_id", sa.String(64), nullable=False),
        sa.Column("external_reference", sa.String(128)),
        sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("document_number", sa.String(128)),
        sa.Column("issued_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="ETB"),
        sa.Column("subtotal", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("tax_total", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("grand_total", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_transaction_id", sa.String(128)),
        sa.Column("irn", sa.String(128)),
        sa.Column("qr_payload", sa.Text()),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("request_payload", sa.JSON()),
        sa.Column("provider_response", sa.JSON()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("registered_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_fiscal_idempotency_key"),
    )
    op.create_index("ix_fiscal_source", "fiscal_documents", ["source_model", "source_record_id"])
    op.create_index("ix_fiscal_status", "fiscal_documents", ["status"])

    op.create_table(
        "payment_transactions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("correlation_id", sa.String(64)),
        sa.Column("reference", sa.String(128), nullable=False),
        sa.Column("source_system", sa.String(32), nullable=False, server_default="odoo"),
        sa.Column("source_model", sa.String(64)),
        sa.Column("source_record_id", sa.String(64)),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="ETB"),
        sa.Column("amount", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("refunded_amount", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_transaction_id", sa.String(128)),
        sa.Column("checkout_url", sa.Text()),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("request_payload", sa.JSON()),
        sa.Column("provider_response", sa.JSON()),
        sa.Column("authorized_at", sa.DateTime(timezone=True)),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_payment_idempotency_key"),
    )
    op.create_index("ix_payment_reference", "payment_transactions", ["reference"])
    op.create_index("ix_payment_status", "payment_transactions", ["status"])

    op.create_table(
        "delivery_orders",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("correlation_id", sa.String(64)),
        sa.Column("reference", sa.String(128), nullable=False),
        sa.Column("source_system", sa.String(32), nullable=False, server_default="odoo"),
        sa.Column("source_model", sa.String(64)),
        sa.Column("source_record_id", sa.String(64)),
        sa.Column("status", sa.String(24), nullable=False, server_default="created"),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_transaction_id", sa.String(128)),
        sa.Column("tracking_number", sa.String(128)),
        sa.Column("tracking_url", sa.Text()),
        sa.Column("currency", sa.String(8), nullable=False, server_default="ETB"),
        sa.Column("price", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("request_payload", sa.JSON()),
        sa.Column("provider_response", sa.JSON()),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_delivery_idempotency_key"),
    )
    op.create_index("ix_delivery_reference", "delivery_orders", ["reference"])
    op.create_index("ix_delivery_status", "delivery_orders", ["status"])

    op.create_table(
        "integration_requests",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("correlation_id", sa.String(64)),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("rail", sa.String(16), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("resource_type", sa.String(32)),
        sa.Column("resource_id", sa.String(32)),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("request_payload", sa.JSON()),
        sa.Column("response_payload", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_request_resource", "integration_requests", ["resource_type", "resource_id"])
    op.create_index("ix_request_idempotency", "integration_requests", ["idempotency_key"])
    op.create_index("ix_request_correlation", "integration_requests", ["correlation_id"])

    op.create_table(
        "integration_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("correlation_id", sa.String(64)),
        sa.Column("rail", sa.String(16), nullable=False),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.String(32), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("from_status", sa.String(24)),
        sa.Column("to_status", sa.String(24)),
        sa.Column("detail", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_event_resource", "integration_events", ["resource_type", "resource_id"])

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("rail", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("external_event_id", sa.String(128), nullable=False),
        sa.Column("signature_valid", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resource_type", sa.String(32)),
        sa.Column("resource_id", sa.String(32)),
        sa.Column("payload", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "external_event_id", name="uq_webhook_provider_event"),
    )
    op.create_index("ix_webhook_rail_provider", "webhook_events", ["rail", "provider"])

    op.create_table(
        "mock_fiscal_registrations",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("provider_transaction_id", sa.String(128), nullable=False),
        sa.Column("irn", sa.String(128), nullable=False),
        sa.Column("qr_payload", sa.Text(), nullable=False),
        sa.Column("document_number", sa.String(128)),
        sa.Column("grand_total", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="registered"),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("idempotency_key", name="uq_mock_fiscal_idempotency"),
    )

    op.create_table(
        "mock_provider_state",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.String(255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("mock_provider_state")
    op.drop_table("mock_fiscal_registrations")
    op.drop_index("ix_webhook_rail_provider", table_name="webhook_events")
    op.drop_table("webhook_events")
    op.drop_index("ix_event_resource", table_name="integration_events")
    op.drop_table("integration_events")
    op.drop_index("ix_request_correlation", table_name="integration_requests")
    op.drop_index("ix_request_idempotency", table_name="integration_requests")
    op.drop_index("ix_request_resource", table_name="integration_requests")
    op.drop_table("integration_requests")
    op.drop_index("ix_delivery_status", table_name="delivery_orders")
    op.drop_index("ix_delivery_reference", table_name="delivery_orders")
    op.drop_table("delivery_orders")
    op.drop_index("ix_payment_status", table_name="payment_transactions")
    op.drop_index("ix_payment_reference", table_name="payment_transactions")
    op.drop_table("payment_transactions")
    op.drop_index("ix_fiscal_status", table_name="fiscal_documents")
    op.drop_index("ix_fiscal_source", table_name="fiscal_documents")
    op.drop_table("fiscal_documents")
    op.drop_index("ix_idempotency_status", table_name="idempotency_records")
    op.drop_table("idempotency_records")
