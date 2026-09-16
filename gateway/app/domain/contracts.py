"""The normalized integration contracts.

These schemas are the stable surface between an ERP (Odoo today, potentially
another ERP later) and the outside world. They are deliberately provider
neutral: no ArifPay field, no MoR field, no Odoo field leaks in here.

Money is exchanged as a decimal string on the way out (JSON floats cannot
represent 6199.99 exactly) while accepting numbers or strings on the way in.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

Money = Annotated[Decimal, Field(max_digits=16, decimal_places=2)]

TOLERANCE = Decimal("0.05")


def _money_out(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{Decimal(value):.2f}"


class GatewayModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Shared value objects
# ---------------------------------------------------------------------------
class SourceRef(GatewayModel):
    """Which ERP record caused this request. Pure provenance, never business logic."""

    system: str = Field(default="odoo", max_length=32)
    model: str = Field(max_length=64, description="e.g. pos.order, account.move")
    record_id: str = Field(max_length=64)
    reference: str | None = Field(default=None, max_length=128)

    @field_validator("record_id", mode="before")
    @classmethod
    def _coerce_record_id(cls, value: Any) -> str:
        return str(value)


class Party(GatewayModel):
    """Seller or buyer. `tin` is the Ethiopian taxpayer identification number."""

    name: str = Field(max_length=200)
    tin: str | None = Field(default=None, max_length=32)
    vat: str | None = Field(default=None, max_length=32)
    address: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=128)


class DocumentLine(GatewayModel):
    line_id: str | None = Field(default=None, max_length=64)
    sku: str | None = Field(default=None, max_length=64, description="Internal reference of the variant")
    barcode: str | None = Field(default=None, max_length=64)
    description: str = Field(max_length=300)
    quantity: Decimal = Field(gt=Decimal("0"))
    unit_price: Money = Field(ge=Decimal("0"))
    discount: Money = Decimal("0")
    tax_code: str | None = Field(default=None, max_length=32)
    tax_rate: Decimal = Decimal("0")
    tax_amount: Money = Decimal("0")
    line_total: Money

    @field_serializer("unit_price", "discount", "tax_amount", "line_total")
    def _ser_money(self, value: Decimal) -> str:
        return _money_out(value)

    @field_serializer("quantity", "tax_rate")
    def _ser_number(self, value: Decimal) -> str:
        return str(Decimal(value).normalize())


class TaxEntry(GatewayModel):
    code: str = Field(max_length=32)
    name: str | None = Field(default=None, max_length=100)
    rate: Decimal = Decimal("0")
    base: Money = Decimal("0")
    amount: Money = Decimal("0")

    @field_serializer("base", "amount")
    def _ser_money(self, value: Decimal) -> str:
        return _money_out(value)

    @field_serializer("rate")
    def _ser_rate(self, value: Decimal) -> str:
        return str(Decimal(value).normalize())


class Totals(GatewayModel):
    currency: str = Field(default="ETB", min_length=3, max_length=8)
    subtotal: Money
    tax_total: Money = Decimal("0")
    grand_total: Money

    @field_serializer("subtotal", "tax_total", "grand_total")
    def _ser_money(self, value: Decimal) -> str:
        return _money_out(value)

    @model_validator(mode="after")
    def _check_consistency(self) -> Totals:
        expected = self.subtotal + self.tax_total
        if abs(expected - self.grand_total) > TOLERANCE:
            raise ValueError(
                f"totals inconsistent: subtotal {self.subtotal} + tax {self.tax_total} "
                f"!= grand_total {self.grand_total}"
            )
        return self


class DocumentMeta(GatewayModel):
    type: Literal["receipt", "invoice", "credit_note", "refund_receipt"] = "receipt"
    number: str | None = Field(default=None, max_length=128)
    issued_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=500)
    # Points back to the original document for credit notes / refunds.
    original_irn: str | None = Field(default=None, max_length=128)


# ---------------------------------------------------------------------------
# Fiscal rail
# ---------------------------------------------------------------------------
class FiscalDocumentRequest(GatewayModel):
    """What an ERP sends to register one fiscal document.

    `idempotency_key` is the stable logical identity of the document. Odoo uses
    the UUID stored on et.fiscal.transaction, which never changes across
    retries, restarts or offline recovery.
    """

    idempotency_key: str = Field(min_length=8, max_length=128)
    source: SourceRef
    document: DocumentMeta = Field(default_factory=DocumentMeta)
    seller: Party
    buyer: Party | None = None
    lines: list[DocumentLine] = Field(min_length=1)
    taxes: list[TaxEntry] = Field(default_factory=list)
    totals: Totals

    @model_validator(mode="after")
    def _check_line_sum(self) -> FiscalDocumentRequest:
        line_sum = sum((line.line_total for line in self.lines), Decimal("0"))
        if abs(line_sum - self.totals.subtotal) > TOLERANCE:
            raise ValueError(
                f"line totals {line_sum} do not match declared subtotal {self.totals.subtotal}"
            )
        return self


class FiscalDocumentResponse(BaseModel):
    """What the ERP stores back against its own record."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    idempotency_key: str
    status: Literal["pending", "submitting", "registered", "failed", "cancelled"]
    provider: str
    provider_transaction_id: str | None = None
    irn: str | None = None
    qr_payload: str | None = None
    document_number: str | None = None
    currency: str = "ETB"
    grand_total: Decimal = Decimal("0")
    attempt_count: int = 0
    last_error: str | None = None
    registered_at: datetime | None = None
    submitted_at: datetime | None = None
    correlation_id: str | None = None
    # True when this response replays a previous identical request rather than
    # performing a new registration. Purely informational for the caller.
    replayed: bool = False

    @field_serializer("grand_total")
    def _ser_money(self, value: Decimal) -> str:
        return _money_out(value)


class FiscalCancelRequest(GatewayModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    reason: str = Field(max_length=300)


# ---------------------------------------------------------------------------
# Payment rail
# ---------------------------------------------------------------------------
class PaymentRequest(GatewayModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    reference: str = Field(max_length=128, description="ERP transaction reference")
    source: SourceRef | None = None
    amount: Money = Field(gt=Decimal("0"))
    currency: str = Field(default="ETB", min_length=3, max_length=8)
    customer: Party | None = None
    description: str | None = Field(default=None, max_length=300)
    return_url: str | None = Field(default=None, max_length=500)
    cancel_url: str | None = Field(default=None, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    idempotency_key: str
    reference: str
    status: Literal[
        "pending", "authorized", "succeeded", "failed", "cancelled", "refunded", "partially_refunded"
    ]
    provider: str
    provider_transaction_id: str | None = None
    checkout_url: str | None = None
    amount: Decimal = Decimal("0")
    refunded_amount: Decimal = Decimal("0")
    currency: str = "ETB"
    last_error: str | None = None
    attempt_count: int = 0
    correlation_id: str | None = None
    replayed: bool = False

    @field_serializer("amount", "refunded_amount")
    def _ser_money(self, value: Decimal) -> str:
        return _money_out(value)


class RefundRequest(GatewayModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    amount: Money | None = Field(default=None, gt=Decimal("0"))
    reason: str | None = Field(default=None, max_length=300)


# ---------------------------------------------------------------------------
# Delivery rail
# ---------------------------------------------------------------------------
class Address(GatewayModel):
    name: str = Field(max_length=200)
    phone: str | None = Field(default=None, max_length=64)
    street: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=100)
    country_code: str | None = Field(default=None, max_length=4)
    latitude: float | None = None
    longitude: float | None = None


class DeliveryPackage(GatewayModel):
    description: str = Field(max_length=300)
    quantity: Decimal = Field(default=Decimal("1"), gt=Decimal("0"))
    weight_kg: Decimal | None = None
    sku: str | None = Field(default=None, max_length=64)


class DeliveryRequest(GatewayModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    reference: str = Field(max_length=128, description="ERP picking reference")
    source: SourceRef | None = None
    pickup: Address
    dropoff: Address
    packages: list[DeliveryPackage] = Field(min_length=1)
    cash_on_delivery: Money = Decimal("0")
    currency: str = Field(default="ETB", min_length=3, max_length=8)
    notes: str | None = Field(default=None, max_length=300)


class DeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    idempotency_key: str
    reference: str
    status: Literal["created", "assigned", "picked_up", "in_transit", "delivered", "failed", "cancelled"]
    provider: str
    provider_transaction_id: str | None = None
    tracking_number: str | None = None
    tracking_url: str | None = None
    price: Decimal = Decimal("0")
    currency: str = "ETB"
    last_error: str | None = None
    correlation_id: str | None = None
    replayed: bool = False

    @field_serializer("price")
    def _ser_money(self, value: Decimal) -> str:
        return _money_out(value)


# ---------------------------------------------------------------------------
# Errors / health
# ---------------------------------------------------------------------------
class ErrorResponse(BaseModel):
    code: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None


class HealthResponse(BaseModel):
    status: Literal["pass", "warn", "fail"]
    service: str
    version: str
    environment: str
    database: str
    providers: dict[str, str] = Field(default_factory=dict)
