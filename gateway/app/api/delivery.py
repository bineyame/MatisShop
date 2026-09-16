"""Delivery rail HTTP API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Header, Path

from app.api.deps import AuthDep, DeliveryServiceDep
from app.domain.contracts import DeliveryRequest, DeliveryResponse
from app.domain.errors import ValidationError

router = APIRouter(prefix="/deliveries", tags=["delivery"], dependencies=[AuthDep])


@router.post("", response_model=DeliveryResponse, summary="Book a delivery")
async def create_delivery(
    service: DeliveryServiceDep,
    payload: Annotated[DeliveryRequest, Body()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> DeliveryResponse:
    if idempotency_key and idempotency_key != payload.idempotency_key:
        raise ValidationError("Idempotency-Key header does not match the body")
    return await service.create(payload)


@router.get("/{order_id}", response_model=DeliveryResponse)
async def get_delivery(
    service: DeliveryServiceDep, order_id: Annotated[str, Path()]
) -> DeliveryResponse:
    return await service.get(order_id)


@router.post("/{order_id}/refresh", response_model=DeliveryResponse)
async def refresh_delivery(
    service: DeliveryServiceDep, order_id: Annotated[str, Path()]
) -> DeliveryResponse:
    """Poll the courier for the current lifecycle state."""
    return await service.refresh_status(order_id)


@router.post("/{order_id}/cancel", response_model=DeliveryResponse)
async def cancel_delivery(
    service: DeliveryServiceDep, order_id: Annotated[str, Path()]
) -> DeliveryResponse:
    return await service.cancel(order_id)
