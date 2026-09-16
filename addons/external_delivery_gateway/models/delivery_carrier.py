"""Odoo delivery carrier backed by the Integration Gateway.

Plugs into Odoo's native `delivery` framework: a `delivery.carrier` with
`delivery_type = 'integration_gateway'` implementing the four hooks Odoo calls
(`_rate_shipment`, `_send_shipping`, `_get_tracking_link`, `_cancel_shipment`).

Which courier is actually behind it is a gateway configuration value.
"""

import json
import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DeliveryGatewayError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class DeliveryCarrier(models.Model):
    _inherit = "delivery.carrier"

    delivery_type = fields.Selection(
        selection_add=[("integration_gateway", "Integration Gateway")],
        ondelete={"integration_gateway": "set default"},
    )

    gateway_base_url = fields.Char(
        string="Gateway URL",
        help="Leave empty to reuse the URL configured for fiscalization.",
    )
    gateway_api_key = fields.Char(
        string="Gateway API Key",
        groups="base.group_system",
        help="Leave empty to reuse the fiscal connector API key.",
    )

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    def _gateway_settings(self):
        self.ensure_one()
        params = self.env["ir.config_parameter"].sudo()
        return {
            "base_url": (
                self.gateway_base_url or params.get_param("et_fiscal.gateway_base_url") or ""
            ).rstrip("/"),
            "api_key": self.sudo().gateway_api_key
            or params.get_param("et_fiscal.gateway_api_key")
            or "",
            "timeout": float(params.get_param("et_fiscal.gateway_timeout") or 20),
        }

    def _gateway_request(self, method, path, payload=None, correlation_id=None):
        self.ensure_one()
        settings = self._gateway_settings()
        if not settings["base_url"]:
            raise UserError(
                _("No Integration Gateway URL configured for carrier %s.") % self.display_name
            )
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-Key": settings["api_key"],
        }
        if correlation_id:
            headers["X-Correlation-Id"] = correlation_id

        try:
            response = requests.request(
                method,
                f"{settings['base_url']}{path}",
                data=json.dumps(payload) if payload is not None else None,
                headers=headers,
                timeout=settings["timeout"],
            )
        except requests.exceptions.RequestException as exc:
            raise DeliveryGatewayError(_("Integration Gateway is unreachable: %s") % exc) from exc

        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400:
            raise DeliveryGatewayError(
                body.get("message") or response.text[:500] or _("unknown gateway error"),
                status_code=response.status_code,
            )
        return body

    # ------------------------------------------------------------------
    # Odoo delivery hooks
    # ------------------------------------------------------------------
    def integration_gateway_rate_shipment(self, order):
        """Quote a delivery for a sale order.

        The mock courier prices by package count, so a quote is derived
        locally rather than booking a real delivery just to get a price.
        """
        self.ensure_one()
        package_count = max(1, len(order.order_line.filtered(lambda line: line.product_id.type == "consu")))
        price = 120.0 + 30.0 * (package_count - 1)
        return {
            "success": True,
            "price": price,
            "error_message": False,
            "warning_message": _(
                "DEMO delivery quote from a mock courier. No courier is dispatched."
            ),
        }

    def integration_gateway_send_shipping(self, pickings):
        """Book a delivery for each picking."""
        results = []
        for picking in pickings:
            payload = picking._gateway_delivery_payload()
            try:
                response = self._gateway_request(
                    "POST",
                    "/api/v1/deliveries",
                    payload,
                    correlation_id=picking.name,
                )
            except DeliveryGatewayError as exc:
                raise UserError(
                    _("Could not book the delivery for %(picking)s: %(error)s")
                    % {"picking": picking.name, "error": exc.message}
                ) from exc

            picking.write({
                "gateway_delivery_id": response.get("id"),
                "gateway_delivery_status": response.get("status"),
                "gateway_delivery_provider": response.get("provider"),
                "carrier_tracking_ref": response.get("tracking_number"),
            })
            results.append({
                "exact_price": float(response.get("price") or 0.0),
                "tracking_number": response.get("tracking_number"),
            })
            _logger.info(
                "booked delivery %s for picking %s (provider=%s)",
                response.get("id"),
                picking.name,
                response.get("provider"),
            )
        return results

    def integration_gateway_get_tracking_link(self, picking):
        if not picking.gateway_delivery_id:
            return False
        try:
            response = self._gateway_request(
                "GET", f"/api/v1/deliveries/{picking.gateway_delivery_id}"
            )
        except DeliveryGatewayError:
            return False
        return response.get("tracking_url") or False

    def integration_gateway_cancel_shipment(self, pickings):
        for picking in pickings:
            if not picking.gateway_delivery_id:
                continue
            try:
                response = self._gateway_request(
                    "POST", f"/api/v1/deliveries/{picking.gateway_delivery_id}/cancel"
                )
            except DeliveryGatewayError as exc:
                raise UserError(
                    _("Could not cancel the delivery for %(picking)s: %(error)s")
                    % {"picking": picking.name, "error": exc.message}
                ) from exc
            picking.write({
                "gateway_delivery_status": response.get("status"),
                "carrier_tracking_ref": False,
            })
        return True

    @api.model
    def action_test_gateway_connection(self):
        health = self._gateway_request("GET", "/health")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if health.get("status") == "pass" else "warning",
                "title": _("Integration Gateway"),
                "message": _("Delivery provider: %s")
                % health.get("providers", {}).get("delivery", "-"),
                "sticky": False,
            },
        }
