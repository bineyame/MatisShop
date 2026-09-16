"""Delivery state on the picking.

The picking is the record a warehouse person actually looks at, so the courier
reference and lifecycle state belong here rather than on a separate
integration screen nobody opens.
"""

import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

from .delivery_carrier import DeliveryGatewayError

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    gateway_delivery_id = fields.Char(
        string="Gateway Delivery",
        readonly=True,
        copy=False,
        help="Resource id of this delivery inside the Integration Gateway",
    )
    gateway_delivery_status = fields.Selection(
        [
            ("created", "Created"),
            ("assigned", "Assigned"),
            ("picked_up", "Picked Up"),
            ("in_transit", "In Transit"),
            ("delivered", "Delivered"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        string="Courier Status",
        readonly=True,
        copy=False,
    )
    gateway_delivery_provider = fields.Char(string="Courier", readonly=True, copy=False)

    def _gateway_delivery_payload(self):
        """Map the picking onto the normalized delivery contract."""
        self.ensure_one()
        source_address = self.picking_type_id.warehouse_id.partner_id or self.company_id.partner_id
        destination = self.partner_id

        packages = []
        for move in self.move_ids:
            packages.append(
                {
                    "description": move.product_id.display_name,
                    "quantity": str(move.product_uom_qty or 1),
                    "sku": move.product_id.default_code or None,
                    "weight_kg": str(move.product_id.weight) if move.product_id.weight else None,
                }
            )
        if not packages:
            packages = [{"description": self.name, "quantity": "1"}]

        return {
            # The picking name is unique and immutable: a safe idempotency key.
            "idempotency_key": f"odoo-delivery-{self.name}",
            "reference": self.name,
            "source": {
                "system": "odoo",
                "model": "stock.picking",
                "record_id": str(self.id),
                "reference": self.name,
            },
            "pickup": self._gateway_address(source_address),
            "dropoff": self._gateway_address(destination),
            "packages": packages,
            "cash_on_delivery": "0.00",
            "currency": self.company_id.currency_id.name,
            "notes": self.note and str(self.note)[:300] or None,
        }

    @staticmethod
    def _gateway_address(partner):
        if not partner:
            return {"name": "Unknown"}
        return {
            "name": partner.name or "Unknown",
            "phone": partner.phone or partner.mobile or None,
            "street": ", ".join(part for part in [partner.street, partner.street2] if part) or None,
            "city": partner.city or None,
            "country_code": partner.country_id.code or None,
        }

    def action_refresh_delivery_status(self):
        """Poll the courier through the gateway."""
        for picking in self:
            if not picking.gateway_delivery_id or not picking.carrier_id:
                continue
            try:
                response = picking.carrier_id._gateway_request(
                    "POST", f"/api/v1/deliveries/{picking.gateway_delivery_id}/refresh"
                )
            except DeliveryGatewayError as exc:
                raise UserError(_("Could not reach the gateway: %s") % exc.message) from exc
            picking.gateway_delivery_status = response.get("status")
            _logger.info(
                "picking %s courier status is now %s", picking.name, response.get("status")
            )
        return True
