"""Inbound provider callbacks.

Rules, in order:

1. Never trust the body. The signature is verified over the RAW bytes before
   anything is parsed.
2. Record the callback either way. A rejected callback is stored with
   ``signature_valid=False`` - probing attempts are exactly what you want
   logged.
3. Deduplicate on (provider, event id). Providers retry; we must not.
4. For money, a webhook is a hint, not a fact: it triggers a verify against
   the provider rather than being applied blindly.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Annotated, Any

from fastapi import APIRouter, Header, Path, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DeliveryServiceDep, PaymentServiceDep, SessionDep, SettingsDep
from app.domain.errors import AuthenticationError, ResourceNotFound, ValidationError
from app.observability import get_logger
from app.persistence.models import WebhookEvent

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

SUPPORTED_RAILS = {"payment", "delivery", "fiscal"}


def verify_signature(secret: str, raw_body: bytes, signature_header: str | None) -> bool:
    """Constant-time HMAC-SHA256 check over the raw request body.

    Accepts ``sha256=<hex>`` or a bare hex digest. A real provider adapter
    overrides this with that provider's own scheme (some sign a canonical
    string, some include a timestamp to defeat replay); the verification seam
    lives here so Odoo never has to care.
    """
    if not signature_header:
        return False
    provided = signature_header.split("=", 1)[1] if "=" in signature_header else signature_header
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided.strip(), expected)


@router.post("/{rail}/{provider}", summary="Provider callback endpoint")
async def receive_webhook(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    payment_svc: PaymentServiceDep,
    delivery_svc: DeliveryServiceDep,
    rail: Annotated[str, Path()],
    provider: Annotated[str, Path()],
    x_signature: Annotated[str | None, Header(alias="X-Signature")] = None,
) -> dict[str, Any]:
    if rail not in SUPPORTED_RAILS:
        raise ValidationError(f"unsupported rail '{rail}'", detail={"supported": sorted(SUPPORTED_RAILS)})

    raw_body = await request.body()
    valid = verify_signature(settings.gateway_webhook_secret, raw_body, x_signature)

    try:
        payload = json.loads(raw_body or b"{}")
    except json.JSONDecodeError:
        payload = {"_unparseable": True}
    if not isinstance(payload, dict):
        payload = {"_value": payload}

    event_id = str(payload.get("event_id") or hashlib.sha256(raw_body).hexdigest()[:32])

    event = WebhookEvent(
        rail=rail,
        provider=provider,
        external_event_id=event_id,
        signature_valid=valid,
        processed=False,
        payload=payload if valid else {"_rejected": True},
    )
    try:
        # add() inside the savepoint: see the note in IdempotencyService.claim.
        async with session.begin_nested():
            session.add(event)
            await session.flush()
    except IntegrityError:
        # Provider retried a callback we already handled.
        stmt = select(WebhookEvent).where(
            WebhookEvent.provider == provider, WebhookEvent.external_event_id == event_id
        )
        existing = (await session.execute(stmt)).scalars().first()
        logger.info(
            "webhook.duplicate",
            extra={"rail": rail, "provider": provider, "external_event_id": event_id},
        )
        return {
            "status": "duplicate",
            "event_id": event_id,
            "processed": bool(existing and existing.processed),
        }

    if not valid:
        logger.warning(
            "webhook.invalid_signature",
            extra={"rail": rail, "provider": provider, "external_event_id": event_id},
        )
        event.error = "invalid signature"
        # Commit before raising: the request will be rolled back by the session
        # dependency, and a rejected callback must still leave a trace.
        await session.commit()
        raise AuthenticationError("invalid webhook signature")

    provider_transaction_id = payload.get("provider_transaction_id")
    status = payload.get("status")
    if not provider_transaction_id or not status:
        event.error = "missing provider_transaction_id or status"
        await session.commit()
        raise ValidationError("payload must contain provider_transaction_id and status")

    try:
        if rail == "payment":
            # A webhook is a trigger, not proof of payment: reconcile against
            # the provider before touching money state.
            transaction = await payment_svc.apply_external_status(provider_transaction_id, status)
            verified = await payment_svc.verify(transaction.id)
            event.resource_type = "payment_transaction"
            event.resource_id = transaction.id
            result_status = verified.status
        elif rail == "delivery":
            order = await delivery_svc.apply_external_status(provider_transaction_id, status)
            event.resource_type = "delivery_order"
            event.resource_id = order.id
            result_status = order.status
        else:
            event.error = "fiscal callbacks are not supported by the bundled providers"
            await session.commit()
            raise ValidationError(event.error)
    except ResourceNotFound as exc:
        event.error = str(exc)
        await session.commit()
        raise

    event.processed = True
    await session.flush()
    logger.info(
        "webhook.processed",
        extra={
            "rail": rail,
            "provider": provider,
            "external_event_id": event_id,
            "resource_id": event.resource_id,
            "to_status": result_status,
        },
    )
    return {
        "status": "processed",
        "event_id": event_id,
        "resource_id": event.resource_id,
        "resource_status": result_status,
    }
