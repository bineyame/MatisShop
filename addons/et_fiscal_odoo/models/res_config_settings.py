"""Settings for the fiscal connector.

Only integration-level settings live here: where the gateway is, how to
authenticate to it, and whether Odoo submits automatically. Provider choice
and provider credentials are NOT here - they belong to the gateway (ADR-005).
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .fiscal_gateway_client import GatewayError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    et_fiscal_enabled = fields.Boolean(
        string="Enable Fiscalization",
        config_parameter="et_fiscal.enabled",
        default=True,
    )
    et_fiscal_auto_submit = fields.Boolean(
        string="Submit Automatically",
        config_parameter="et_fiscal.auto_submit",
        default=True,
        help="Submit as soon as the document is paid/posted. When off, documents "
        "are queued and submitted by the scheduled job.",
    )
    et_fiscal_gateway_base_url = fields.Char(
        string="Integration Gateway URL",
        config_parameter="et_fiscal.gateway_base_url",
    )
    et_fiscal_gateway_api_key = fields.Char(
        string="Gateway API Key",
        config_parameter="et_fiscal.gateway_api_key",
        help="Shared secret sent as X-API-Key. Seeded from the GATEWAY_API_KEY "
        "environment variable at install time.",
    )
    et_fiscal_gateway_timeout = fields.Char(
        string="Gateway Timeout (seconds)",
        config_parameter="et_fiscal.gateway_timeout",
        default="20",
    )
    et_fiscal_max_auto_retries = fields.Char(
        string="Maximum Automatic Retries",
        config_parameter="et_fiscal.max_auto_retries",
        default="10",
        help="How many times the scheduled job retries one document before it "
        "needs a human. The identity never changes, so retrying is always safe.",
    )

    @api.model
    def action_test_gateway_connection(self):
        """Ping the gateway and report what it says."""
        try:
            health = self.env["et.fiscal.gateway.client"].health()
        except GatewayError as exc:
            raise UserError(
                _("Could not reach the Integration Gateway:\n\n%s") % exc.message
            ) from exc

        providers = health.get("providers", {})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if health.get("status") == "pass" else "warning",
                "title": _("Integration Gateway"),
                "message": _(
                    "Status: %(status)s\nDatabase: %(database)s\n"
                    "Fiscal provider: %(fiscal)s\nPayment provider: %(payment)s\n"
                    "Delivery provider: %(delivery)s"
                )
                % {
                    "status": health.get("status"),
                    "database": health.get("database"),
                    "fiscal": providers.get("fiscal", "-"),
                    "payment": providers.get("payment", "-"),
                    "delivery": providers.get("delivery", "-"),
                },
                "sticky": False,
            },
        }
