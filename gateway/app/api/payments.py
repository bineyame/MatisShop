"""Payment rail HTTP API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Header, Path

from app.api.deps import AuthDep, PaymentServiceDep
from app.domain.contracts import PaymentRequest, PaymentResponse, RefundRequest
from app.domain.errors import ValidationError

router = APIRouter(prefix="/payments", tags=["payments"], dependencies=[AuthDep])


@router.post("", response_model=PaymentResponse, summary="Create a payment")
async def create_payment(
    service: PaymentServiceDep,
    payload: Annotated[PaymentRequest, Body()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PaymentResponse:
    if idempotency_key and idempotency_key != payload.idempotency_key:
        raise ValidationError("Idempotency-Key header does not match the body")
    return await service.create(payload)


@router.get("/{transaction_id}", response_model=PaymentResponse)
async def get_payment(
    service: PaymentServiceDep, transaction_id: Annotated[str, Path()]
) -> PaymentResponse:
    return await service.get(transaction_id)


@router.get("/by-reference/{reference}", response_model=PaymentResponse)
async def get_payment_by_reference(
    service: PaymentServiceDep, reference: Annotated[str, Path()]
) -> PaymentResponse:
    """Odoo polls this with its own payment.transaction reference."""
    return await service.get_by_reference(reference)


@router.post("/{transaction_id}/verify", response_model=PaymentResponse)
async def verify_payment(
    service: PaymentServiceDep, transaction_id: Annotated[str, Path()]
) -> PaymentResponse:
    """Authoritative check against the provider. Never trust a webhook alone."""
    return await service.verify(transaction_id)


@router.post("/{transaction_id}/refund", response_model=PaymentResponse)
async def refund_payment(
    service: PaymentServiceDep,
    transaction_id: Annotated[str, Path()],
    payload: Annotated[RefundRequest, Body()],
) -> PaymentResponse:
    return await service.refund(transaction_id, payload)
