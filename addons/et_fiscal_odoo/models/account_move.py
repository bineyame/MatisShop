"""Customer invoices and credit notes become fiscal documents.

This is the credit-wholesale path: a sales order is delivered and invoiced,
the invoice is posted, and posting is what triggers fiscalization - not
payment. The fiscal document describes the invoice; settlement is a separate
concern on the payment rail.
"""

import logging

from odoo import models

_logger = logging.getLogger(__name__)

FISCAL_MOVE_TYPES = ("out_invoice", "out_refund")


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "et.fiscal.document.mixin"]

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        for move in posted:
            if move.move_type not in FISCAL_MOVE_TYPES:
                continue
            # A POS order that generates an invoice is already fiscalized as a
            # receipt; do not register the same sale twice.
            if move._fiscal_originates_from_pos():
                continue
            try:
                transaction = move._fiscal_ensure_transaction()
                move._fiscal_submit_now(transaction)
            except Exception:  # noqa: BLE001 - posting must not fail on fiscalization
                _logger.exception("could not create fiscal transaction for invoice %s", move.name)
        return posted

    def _fiscal_originates_from_pos(self):
        self.ensure_one()
        pos_orders = getattr(self, "pos_order_ids", False)
        return bool(pos_orders)

    # ------------------------------------------------------------------
    # Mixin implementation
    # ------------------------------------------------------------------
    def _fiscal_document_type(self):
        self.ensure_one()
        return "credit_note" if self.move_type == "out_refund" else "invoice"

    def _fiscal_should_register(self):
        self.ensure_one()
        return (
            self.move_type in FISCAL_MOVE_TYPES
            and self.state == "posted"
            and self.amount_total != 0
        )

    def _fiscal_amount_untaxed(self):
        return self.amount_untaxed

    def _fiscal_amount_tax(self):
        return self.amount_tax

    def _fiscal_amount_total(self):
        return self.amount_total

    def _prepare_fiscal_document(self):
        self.ensure_one()
        currency = self.currency_id

        lines = []
        for line in self.invoice_line_ids.filtered(lambda line: line.display_type == "product"):
            tax_rate = sum(line.tax_ids.mapped("amount")) if line.tax_ids else 0.0
            line_tax = line.price_total - line.price_subtotal
            lines.append(
                {
                    "line_id": str(line.id),
                    "sku": line.product_id.default_code or None,
                    "barcode": line.product_id.barcode or None,
                    "description": line.name or line.product_id.display_name or "Item",
                    "quantity": str(line.quantity),
                    "unit_price": self._fiscal_round(currency, line.price_unit),
                    "discount": self._fiscal_round(
                        currency, line.price_unit * line.quantity * (line.discount or 0.0) / 100.0
                    ),
                    "tax_code": ",".join(line.tax_ids.mapped("name")) or None,
                    "tax_rate": str(tax_rate),
                    "tax_amount": self._fiscal_round(currency, line_tax),
                    "line_total": self._fiscal_round(currency, line.price_subtotal),
                }
            )

        taxes = []
        groups = {}
        for line in self.invoice_line_ids.filtered(lambda line: line.display_type == "product"):
            line_tax = line.price_total - line.price_subtotal
            for tax in line.tax_ids:
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
        for group in groups.values():
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
                "number": self.name,
                "issued_at": (self.invoice_date or self.date).isoformat(),
                "notes": self.narration and str(self.narration)[:500] or None,
                "original_irn": self.reversed_entry_id.fiscal_irn or None
                if self.reversed_entry_id
                else None,
            },
            "seller": self._fiscal_seller(),
            "buyer": self._fiscal_buyer(self.partner_id),
            "lines": lines,
            "taxes": taxes,
            "totals": {
                "currency": currency.name,
                "subtotal": self._fiscal_round(currency, self.amount_untaxed),
                "tax_total": self._fiscal_round(currency, self.amount_tax),
                "grand_total": self._fiscal_round(currency, self.amount_total),
            },
        }
