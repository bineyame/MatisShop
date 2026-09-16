"""POS orders become fiscal receipts.

Trigger strategy, chosen for robustness rather than elegance:

* ``action_pos_order_paid`` is the hook. It is called on every path that makes
  an order paid - the POS front end syncing an order, a programmatic order,
  a reprint/recovery - and it has been stable across Odoo versions.
* a cron sweep catches anything the hook missed (a session closed while the
  gateway was down, an order synced during a restart).

Both paths funnel through ``_fiscal_ensure_transaction()``, which is protected
by a unique constraint, so a double trigger cannot create two registrations.
"""

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _name = "pos.order"
    _inherit = ["pos.order", "et.fiscal.document.mixin"]

    # ------------------------------------------------------------------
    # Triggers
    # ------------------------------------------------------------------
    def action_pos_order_paid(self):
        result = super().action_pos_order_paid()
        for order in self:
            try:
                transaction = order._fiscal_ensure_transaction()
                order._fiscal_submit_now(transaction)
            except Exception:  # noqa: BLE001 - a fiscal problem never blocks a sale
                _logger.exception("could not create fiscal transaction for POS order %s", order.name)
        return result

    # ------------------------------------------------------------------
    # Mixin implementation
    # ------------------------------------------------------------------
    def _fiscal_document_type(self):
        self.ensure_one()
        return "refund_receipt" if self.amount_total < 0 else "receipt"

    def _fiscal_should_register(self):
        self.ensure_one()
        return self.state in ("paid", "done", "invoiced") and self.amount_total != 0

    def _fiscal_currency(self):
        self.ensure_one()
        return self.currency_id or self.config_id.currency_id or self.company_id.currency_id

    def _fiscal_amount_untaxed(self):
        self.ensure_one()
        return self.amount_total - self.amount_tax

    def _fiscal_amount_tax(self):
        self.ensure_one()
        return self.amount_tax

    def _fiscal_amount_total(self):
        self.ensure_one()
        return self.amount_total

    @staticmethod
    def _fiscal_line_taxes(line):
        """Taxes actually applied to a POS line.

        ``tax_ids_after_fiscal_position`` is the correct field, but it is a
        computed convenience that has moved around between versions; fall back
        to the raw taxes rather than crash a sale.
        """
        return getattr(line, "tax_ids_after_fiscal_position", False) or line.tax_ids

    def _prepare_fiscal_document(self):
        """Map a POS order onto the normalized fiscal contract."""
        self.ensure_one()
        currency = self._fiscal_currency()

        lines = []
        for index, line in enumerate(self.lines, start=1):
            line_tax = line.price_subtotal_incl - line.price_subtotal
            line_taxes = self._fiscal_line_taxes(line)
            tax_rate = sum(line_taxes.mapped("amount")) if line_taxes else 0.0
            lines.append(
                {
                    "line_id": str(line.id or index),
                    "sku": line.product_id.default_code or None,
                    "barcode": line.product_id.barcode or None,
                    "description": line.full_product_name or line.product_id.display_name,
                    "quantity": str(line.qty),
                    "unit_price": self._fiscal_round(currency, line.price_unit),
                    "discount": self._fiscal_round(
                        currency, line.price_unit * line.qty * (line.discount or 0.0) / 100.0
                    ),
                    "tax_code": ",".join(line_taxes.mapped("name")) or None,
                    "tax_rate": str(tax_rate),
                    "tax_amount": self._fiscal_round(currency, line_tax),
                    "line_total": self._fiscal_round(currency, line.price_subtotal),
                }
            )

        taxes = []
        for group in self._fiscal_tax_breakdown():
            taxes.append(
                {
                    "code": group["code"],
                    "name": group["name"],
                    "rate": str(group["rate"]),
                    "base": self._fiscal_round(currency, group["base"]),
                    "amount": self._fiscal_round(currency, group["amount"]),
                }
            )

        return {
            "document": {
                "type": self._fiscal_document_type(),
                "number": self.pos_reference or self.name,
                "issued_at": (self.date_order or self.create_date).isoformat(),
                "notes": getattr(self, "note", False) or None,
            },
            "seller": self._fiscal_seller(),
            "buyer": self._fiscal_buyer(self.partner_id),
            "lines": lines,
            "taxes": taxes,
            "totals": {
                "currency": currency.name,
                "subtotal": self._fiscal_round(currency, self.amount_total - self.amount_tax),
                "tax_total": self._fiscal_round(currency, self.amount_tax),
                "grand_total": self._fiscal_round(currency, self.amount_total),
            },
        }

    def _fiscal_tax_breakdown(self):
        """Aggregate the order's taxes by tax, for the fiscal tax block."""
        self.ensure_one()
        groups = {}
        for line in self.lines:
            line_tax = line.price_subtotal_incl - line.price_subtotal
            for tax in self._fiscal_line_taxes(line):
                entry = groups.setdefault(
                    tax.id,
                    {
                        "code": tax.description or tax.name,
                        "name": tax.name,
                        "rate": tax.amount,
                        "base": 0.0,
                        "amount": 0.0,
                    },
                )
                entry["base"] += line.price_subtotal
                entry["amount"] += line_tax
        return list(groups.values())

    # ------------------------------------------------------------------
    # Safety net
    # ------------------------------------------------------------------
    def action_fiscal_reprint(self):
        """Open the fiscal receipt report (status, IRN, QR)."""
        return self.env.ref("et_fiscal_odoo.action_report_et_fiscal_receipt").report_action(self)


class PosSession(models.Model):
    _inherit = "pos.session"

    def action_pos_session_closing_control(self, *args, **kwargs):
        """Make sure nothing leaves a session unfiscalized."""
        result = super().action_pos_session_closing_control(*args, **kwargs)
        for session in self:
            for order in session.order_ids.filtered(
                lambda o: o.state in ("paid", "done", "invoiced")
            ):
                try:
                    transaction = order._fiscal_ensure_transaction()
                    order._fiscal_submit_now(transaction)
                except Exception:  # noqa: BLE001 - closing a session must not fail on this
                    _logger.exception(
                        "could not fiscalize order %s while closing session %s",
                        order.name,
                        session.name,
                    )
        return result
