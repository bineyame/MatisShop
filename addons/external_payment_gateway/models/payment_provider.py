"""Odoo payment provider backed by the Integration Gateway.

This plugs into Odoo's NATIVE payment framework rather than inventing a
parallel payment system: one ``payment.provider`` record, ordinary
``payment.transaction`` records, ordinary Odoo payment flows.

Which real rail is behind it - ArifPay, Chapa, Telebirr, a card acquirer - is
a gateway configuration value. Odoo never learns the difference, which is the
entire point (ADR-005).
"""

import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    code = fields.Selection(
        selection_add=[("integration_gateway", "Integration Gateway")],
        ondelete={"integration_gateway": "set default"},
    )

    gateway_base_url = fields.Char(
        string="Gateway URL",
        help="Base URL of the Integration Gateway. Leave empty to reuse the URL "
        "configured for fiscalization.",
    )
    gateway_api_key = fields.Char(
        string="Gateway API Key",
        groups="base.group_system",
        help="Shared secret sent as X-API-Key. Leave empty to reuse the fiscal key.",
    )

    @api.model
    def _get_compatible_providers(self, *args, **kwargs):
        return super()._get_compatible_providers(*args, **kwargs)

    def _get_default_payment_method_codes(self):
        self.ensure_one()
        if self.code != "integration_gateway":
            return super()._get_default_payment_method_codes()
        return ["integration_gateway"]

    # ------------------------------------------------------------------
    # Connection settings
    # ------------------------------------------------------------------
    def _gateway_settings(self):
        """Fall back to the fiscal connector's settings.

        One gateway, one API key: duplicating them per rail is a configuration
        trap, not a feature.
        """
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

    def action_test_gateway_connection(self):
        self.ensure_one()
        health = self.env["payment.gateway.client"].health(self)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if health.get("status") == "pass" else "warning",
                "title": _("Integration Gateway"),
                "message": _("Status: %(status)s, payment provider: %(provider)s")
                % {
                    "status": health.get("status"),
                    "provider": health.get("providers", {}).get("payment", "-"),
                },
                "sticky": False,
            },
        }
