"""Operator-facing integration status.

Answers, without opening a container log:

    Which payment / fiscal / delivery provider is live right now?
    Is the gateway reachable?
    How many fiscal documents are registered, waiting or failed?
    For this sale: what happened on each rail?

Provider selection lives in the gateway's environment by design (ADR-005), so
this screen reads the effective values back from the gateway's own /health
endpoint and shows them read-only. **It never displays a secret** - the health
endpoint returns provider names, never credentials.
"""

import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class MatiIntegrationStatus(models.TransientModel):
    _name = "mati.integration.status"
    _description = "Integration Status"

    name = fields.Char(default="Integration Status", readonly=True)

    gateway_url = fields.Char(readonly=True)
    gateway_reachable = fields.Boolean(readonly=True)
    gateway_message = fields.Char(readonly=True)

    fiscal_provider = fields.Char(readonly=True)
    payment_provider = fields.Char(readonly=True)
    delivery_provider = fields.Char(readonly=True)
    fiscal_failure_mode = fields.Boolean(
        string="Mock Fiscal Failure Mode", readonly=True,
        help="Demo control: when on, the mock fiscal provider rejects every registration.",
    )

    fiscal_registered = fields.Integer(readonly=True)
    fiscal_pending = fields.Integer(readonly=True)
    fiscal_failed = fields.Integer(readonly=True)

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        values.update(self._collect())
        return values

    @api.model
    def _collect(self):
        """Gather live status. Never raises - this is a status screen."""
        Transaction = self.env["et.fiscal.transaction"]
        values = {
            "gateway_reachable": False,
            "gateway_message": _("Not checked"),
            "fiscal_provider": "-",
            "payment_provider": "-",
            "delivery_provider": "-",
            "fiscal_registered": Transaction.search_count([("state", "=", "registered")]),
            "fiscal_pending": Transaction.search_count(
                [("state", "in", ("draft", "pending", "submitting"))]
            ),
            "fiscal_failed": Transaction.search_count([("state", "=", "failed")]),
        }

        try:
            config = self.env["et.fiscal.gateway.client"]._get_config()
            values["gateway_url"] = config["base_url"]
        except Exception as exc:  # noqa: BLE001
            values["gateway_message"] = _("Gateway not configured: %s") % exc
            return values

        try:
            health = self.env["et.fiscal.gateway.client"].health()
        except Exception as exc:  # noqa: BLE001
            values["gateway_message"] = _("Unreachable: %s") % exc
            return values

        providers = health.get("providers", {})
        values.update(
            {
                "gateway_reachable": health.get("status") == "pass",
                "gateway_message": _("Status: %(status)s, database %(db)s")
                % {"status": health.get("status"), "db": health.get("database")},
                "fiscal_provider": providers.get("fiscal", "-"),
                "payment_provider": providers.get("payment", "-"),
                "delivery_provider": providers.get("delivery", "-"),
                "fiscal_failure_mode": self.env["mati.demo.setup"]._get_fiscal_failure_mode(),
            }
        )
        return values

    def action_refresh(self):
        self.ensure_one()
        self.write(self._collect())
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_toggle_fiscal_failure(self):
        self.ensure_one()
        self.env["mati.demo.setup"].action_toggle_fiscal_failure()
        return self.action_refresh()

    def action_open_fiscal_transactions(self):
        return self.env.ref("et_fiscal_odoo.action_et_fiscal_transaction").read()[0]

    def action_open_failed_fiscal(self):
        return self.env.ref("et_fiscal_odoo.action_et_fiscal_transaction_failed").read()[0]
