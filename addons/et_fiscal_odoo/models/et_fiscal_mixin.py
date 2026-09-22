"""Mixin implemented by anything that can be fiscalized.

Adding a new fiscalizable document type means inheriting this mixin and
implementing ``_prepare_fiscal_document()``. Nothing else changes - not the
transaction model, not the gateway, not the provider.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class EtFiscalDocumentMixin(models.AbstractModel):
    _name = "et.fiscal.document.mixin"
    _description = "Fiscalizable Document Mixin"

    # Many2many rather than One2many: there is no inverse field on
    # et.fiscal.transaction (it stores model+id, not a typed relation), and a
    # computed One2many without an inverse name is not portable.
    fiscal_transaction_ids = fields.Many2many(
        "et.fiscal.transaction",
        compute="_compute_fiscal_transaction_ids",
        string="Fiscal Transactions",
    )
    fiscal_transaction_id = fields.Many2one(
        "et.fiscal.transaction", compute="_compute_fiscal_transaction_ids"
    )
    # Computed alongside the transaction rather than declared `related`:
    # a related field hanging off a non-stored, non-searchable compute makes
    # Odoo warn that it cannot work out what to recompute, on every start.
    # Reading them through in one pass is both quieter and cheaper.
    fiscal_state = fields.Selection(
        [
            ("draft", "Draft"),
            ("pending", "Pending"),
            ("submitting", "Submitting"),
            ("registered", "Registered"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        string="Fiscal Status",
        compute="_compute_fiscal_transaction_ids",
        readonly=True,
    )
    fiscal_irn = fields.Char(
        string="IRN", compute="_compute_fiscal_transaction_ids", readonly=True
    )
    fiscal_qr_payload = fields.Text(
        compute="_compute_fiscal_transaction_ids", readonly=True
    )

    def _compute_fiscal_transaction_ids(self):
        Transaction = self.env["et.fiscal.transaction"]
        for record in self:
            transactions = Transaction.search(
                [("source_model", "=", record._name), ("source_record_id", "=", record.id)]
            )
            current = transactions[:1]
            record.fiscal_transaction_ids = transactions
            record.fiscal_transaction_id = current
            record.fiscal_state = current.state if current else False
            record.fiscal_irn = current.irn if current else False
            record.fiscal_qr_payload = current.qr_payload if current else False

    # ------------------------------------------------------------------
    # To implement per document type
    # ------------------------------------------------------------------
    def _prepare_fiscal_document(self):
        """Return the normalized contract body for this record.

        Must return a dict with ``document``, ``seller``, ``buyer``, ``lines``,
        ``taxes`` and ``totals``. The envelope (``idempotency_key``, ``source``)
        is added by ``et.fiscal.transaction``.
        """
        raise NotImplementedError(
            f"{self._name} must implement _prepare_fiscal_document()"
        )

    def _fiscal_document_type(self):
        return "receipt"

    def _fiscal_should_register(self):
        """Whether this record is ready to be fiscalized."""
        return True

    # ------------------------------------------------------------------
    # Shared helpers for building contract fragments
    # ------------------------------------------------------------------
    def _fiscal_seller(self):
        """Seller identity, taken from the company of the document."""
        self.ensure_one()
        company = self.company_id or self.env.company
        if not company.et_fiscal_tin:
            raise UserError(
                _(
                    "Company %s has no Taxpayer Identification Number (TIN). "
                    "Set it under Settings > Companies before fiscalizing."
                )
                % company.display_name
            )
        return {
            "name": company.name,
            "tin": company.et_fiscal_tin,
            "vat": company.vat or None,
            "address": ", ".join(
                part
                for part in [company.street, company.street2, company.city, company.country_id.name]
                if part
            )
            or None,
            "phone": company.phone or None,
            "email": company.email or None,
        }

    def _fiscal_buyer(self, partner):
        if not partner:
            return {"name": "Walk-in Customer"}
        return {
            "name": partner.name or "Customer",
            "tin": getattr(partner, "et_fiscal_tin", False) or None,
            "vat": partner.vat or None,
            "address": ", ".join(
                part
                for part in [partner.street, partner.street2, partner.city, partner.country_id.name]
                if part
            )
            or None,
            "phone": partner.phone or partner.mobile or None,
            "email": partner.email or None,
        }

    @api.model
    def _fiscal_round(self, currency, amount):
        """Round to the currency precision and emit a plain 2dp string.

        Money crosses the boundary as a string on purpose: JSON floats cannot
        represent 6199.99 exactly and a fiscal total that is off by a cent is
        a rejected document.
        """
        rounded = currency.round(amount) if currency else round(amount, 2)
        return f"{rounded:.2f}"

    def _fiscal_ensure_transaction(self, document_type=None):
        """Create the fiscal transaction for this record if it has none.

        Idempotent by construction: the unique constraint on
        (source_model, source_record_id, document_type) means a double trigger
        cannot produce two fiscal transactions for one document.
        """
        self.ensure_one()
        Transaction = self.env["et.fiscal.transaction"].sudo()
        if not Transaction._fiscalization_enabled():
            return Transaction

        document_type = document_type or self._fiscal_document_type()
        existing = Transaction.search(
            [
                ("source_model", "=", self._name),
                ("source_record_id", "=", self.id),
                ("document_type", "=", document_type),
            ],
            limit=1,
        )
        if existing:
            return existing
        if not self._fiscal_should_register():
            return Transaction

        values = self._fiscal_transaction_values(document_type)
        transaction = Transaction.create(values)
        _logger.info(
            "created fiscal transaction %s for %s,%s (idempotency_key=%s)",
            transaction.name,
            self._name,
            self.id,
            transaction.idempotency_key,
        )
        return transaction

    def _fiscal_transaction_values(self, document_type):
        """Values for the fiscal transaction. Override to refine amounts."""
        self.ensure_one()
        currency = self._fiscal_currency()
        return {
            "source_model": self._name,
            "source_record_id": self.id,
            "source_reference": self.display_name,
            "document_type": document_type,
            "company_id": (self.company_id or self.env.company).id,
            "currency_id": currency.id,
            "amount_untaxed": self._fiscal_amount_untaxed(),
            "amount_tax": self._fiscal_amount_tax(),
            "amount_total": self._fiscal_amount_total(),
            "state": "pending",
        }

    def _fiscal_currency(self):
        return self.currency_id or (self.company_id or self.env.company).currency_id

    def _fiscal_amount_untaxed(self):
        return 0.0

    def _fiscal_amount_tax(self):
        return 0.0

    def _fiscal_amount_total(self):
        return 0.0

    def _fiscal_submit_now(self, transaction):
        """Submit immediately unless auto-submit is switched off.

        Failure here is swallowed on purpose: a fiscal provider outage must
        never block a sale. The transaction stays retryable and the cron
        drains it later.
        """
        if not transaction:
            return
        if not self.env["et.fiscal.transaction"]._auto_submit_enabled():
            return
        try:
            transaction.sudo()._submit()
        except Exception:  # noqa: BLE001 - never block the business transaction
            _logger.exception(
                "fiscal submission of %s failed; it stays retryable", transaction.name
            )

    def action_fiscalize(self):
        """Manual trigger, available from the source document form."""
        for record in self:
            transaction = record._fiscal_ensure_transaction()
            if transaction:
                transaction.sudo()._submit()
        return True

    def action_view_fiscal_transaction(self):
        self.ensure_one()
        transaction = self.fiscal_transaction_id
        if not transaction:
            raise UserError(_("This document has no fiscal transaction yet."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "et.fiscal.transaction",
            "res_id": transaction.id,
            "view_mode": "form",
            "target": "current",
        }
