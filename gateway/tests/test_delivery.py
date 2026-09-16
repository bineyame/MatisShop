"""Delivery rail: contract, idempotency, lifecycle, cancellation."""

from __future__ import annotations

from sqlalchemy import func, select

from app.persistence.models import DeliveryOrder
from tests.conftest import delivery_payload


async def test_create_delivery_returns_tracking_details(client):
    response = await client.post("/api/v1/deliveries", json=delivery_payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "created"
    assert body["provider"] == "mock"
    assert body["tracking_number"].startswith("MD")
    assert body["tracking_url"]
    assert body["price"] == "120.00"


async def test_delivery_is_idempotent(client, session):
    payload = delivery_payload()
    first = (await client.post("/api/v1/deliveries", json=payload)).json()
    second = (await client.post("/api/v1/deliveries", json=payload)).json()

    assert first["id"] == second["id"]
    assert first["tracking_number"] == second["tracking_number"]
    assert second["replayed"] is True

    count = await session.scalar(select(func.count()).select_from(DeliveryOrder))
    assert count == 1


async def test_status_advances_through_the_normalized_lifecycle(client):
    created = (await client.post("/api/v1/deliveries", json=delivery_payload())).json()
    observed = [created["status"]]
    for _ in range(4):
        response = await client.post(f"/api/v1/deliveries/{created['id']}/refresh")
        observed.append(response.json()["status"])

    assert observed == ["created", "assigned", "picked_up", "in_transit", "delivered"]


async def test_delivered_order_stays_delivered(client):
    created = (await client.post("/api/v1/deliveries", json=delivery_payload())).json()
    for _ in range(6):
        response = await client.post(f"/api/v1/deliveries/{created['id']}/refresh")
    assert response.json()["status"] == "delivered"


async def test_delivery_can_be_cancelled_before_pickup(client):
    created = (await client.post("/api/v1/deliveries", json=delivery_payload())).json()
    response = await client.post(f"/api/v1/deliveries/{created['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


async def test_delivered_order_cannot_be_cancelled(client):
    created = (await client.post("/api/v1/deliveries", json=delivery_payload())).json()
    for _ in range(4):
        await client.post(f"/api/v1/deliveries/{created['id']}/refresh")

    response = await client.post(f"/api/v1/deliveries/{created['id']}/cancel")
    assert response.status_code == 409
    assert response.json()["code"] == "invalid_state_transition"


async def test_at_least_one_package_is_required(client):
    payload = delivery_payload()
    payload["packages"] = []
    response = await client.post("/api/v1/deliveries", json=payload)
    assert response.status_code == 422


async def test_price_scales_with_package_count(client):
    payload = delivery_payload()
    payload["packages"].append(
        {"description": "Nike Air Max / White / 41", "quantity": "1", "sku": "AIR-WHT-41"}
    )
    body = (await client.post("/api/v1/deliveries", json=payload)).json()
    assert body["price"] == "150.00"
