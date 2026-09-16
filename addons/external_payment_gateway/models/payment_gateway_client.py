"""HTTP client for the payment rail.

Mirrors et.fiscal.gateway.client: the only Odoo code that knows the gateway
speaks HTTP, and it knows nothing about any payment provider.
"""

import json
import logging

import requests

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PaymentGatewayError(Exception):
    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload or {}


class PaymentGatewayClient(models.AbstractModel):
    _name = "payment.gateway.client"
    _description = "Integration Gateway client for the payment rail"

    @api.model
    def _request(self, provider, method, path, payload=None, correlation_id=None):
        settings = provider._gateway_settings()
        if not settings["base_url"]:
            raise UserError(
                _("No Integration Gateway URL configured for payment provider %s.")
                % provider.display_name
            )

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-Key": settings["api_key"],
        }
        if correlation_id:
            headers["X-Correlation-Id"] = correlation_id

        _logger.info("payment gateway request %s %s", method, path)
        try:
            response = requests.request(
                method,
                f"{settings['base_url']}{path}",
                data=json.dumps(payload) if payload is not None else None,
                headers=headers,
                timeout=settings["timeout"],
            )
        except requests.exceptions.RequestException as exc:
            raise PaymentGatewayError(
                _("Integration Gateway is unreachable: %s") % exc
            ) from exc

        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.status_code >= 400:
            message = body.get("message") or response.text[:500] or _("unknown gateway error")
            raise PaymentGatewayError(message, status_code=response.status_code, payload=body)
        return body

    @api.model
    def create_payment(self, provider, payload, correlation_id=None):
        return self._request(
            provider, "POST", "/api/v1/payments", payload, correlation_id=correlation_id
        )

    @api.model
    def get_payment(self, provider, payment_id):
        return self._request(provider, "GET", f"/api/v1/payments/{payment_id}")

    @api.model
    def get_payment_by_reference(self, provider, reference):
        return self._request(provider, "GET", f"/api/v1/payments/by-reference/{reference}")

    @api.model
    def verify_payment(self, provider, payment_id):
        return self._request(provider, "POST", f"/api/v1/payments/{payment_id}/verify")

    @api.model
    def refund_payment(self, provider, payment_id, payload):
        return self._request(provider, "POST", f"/api/v1/payments/{payment_id}/refund", payload)

    @api.model
    def health(self, provider):
        return self._request(provider, "GET", "/health")
