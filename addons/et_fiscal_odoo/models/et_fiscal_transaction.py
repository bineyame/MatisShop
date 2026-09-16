"""The fiscal transaction: Odoo's own record of one fiscal document.

Why a dedicated model rather than fields on pos.order / account.move:

* fiscalization has its own lifecycle that does not match the source document
  (a paid order whose fiscal registration is still pending is normal);
* it must survive provider outages and be retried independently;
* failure history must be preserved for audit;
* the same model serves POS orders, invoices and anything else added later,
  through ``et.fiscal.document.mixin``.

State machine (ADR-004):

    draft -> pending -> submitting -> registered -> cancelled
                            |
                            +------> failed -> submitting (retry)

``idempotency_key`` is generated once, at creation, and never changes. It is
the stable logical identity of the document: every retry, every restart and
every offline recovery reuses it, which is what guarantees exactly one
registration (ADR-007).
"""

import json
import logging
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .fiscal_gateway_client import GatewayError

_logger = logging.getLogger(__name__)

# Declared transitions. Anything not listed here is refused.
ALLOWED_TRANSITIONS = {
    "draft": {"pending", "cancelled"},
    "pending": {"submitting", "cancelled"},
    "submitting": {"registered", "failed"},
    "failed": {"submitting", "cancelled"},
    "registered": {"cancelled"},
    "cancelled": set(),
}

TERMINAL_STATES = {"registered", "cancelled"}
RETRYABLE_STATES = {"pending", "failed"}


class EtFiscalTransaction(models.Model):
    _name = "et.fiscal.transaction"
    _description = "Ethiopian Fiscal Transaction"
    _inherit = ["mail.thread"]
    _order = "create_date desc, id desc"
    _rec_name = "name"

    name = fields.Char(
        string="Reference", required=True, copy=False, readonly=True, default=lambda self: _("New")
    )
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, default=lambda self: self.env.company
    )

    # -- provenance: which Odoo record caused this -----------------------------
    source_model = fields.Char(required=True, readonly=True, index=True)
    source_record_id = fields.Integer(required=True, readonly=True, index=True)
    source_reference = fields.Char(readonly=True, help="Human-readable name of the source document")
    document_type = fields.Selection(
        [
            ("receipt", "POS Receipt"),
            ("invoice", "Invoice"),
            ("credit_note", "Credit Note"),
            ("refund_receipt", "Refund Receipt"),
        ],
        required=True,
        default="receipt",
        readonly=True,
    )

    # -- identity --------------------------------------------------------------
    idempotency_key = fields.Char(
        required=True,
        readonly=True,
        copy=False,
        index=True,
        help="Stable logical identity of this fiscal document. Never changes, "
        "so retries can never produce a second registration.",
    )

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("pending", "Pending"),
            ("submitting", "Submitting"),
            ("registered", "Registered"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        readonly=True,
        tracking=True,
        index=True,
    )

    # -- amounts ---------------------------------------------------------------
    currency_id = fields.Many2one("res.currency", required=True, readonly=True)
    amount_untaxed = fields.Monetary(readonly=True, currency_field="currency_id")
    amount_tax = fields.Monetary(readonly=True, currency_field="currency_id")
    amount_total = fields.Monetary(readonly=True, currency_field="currency_id")

    # -- gateway / provider result --------------------------------------------
    provider = fields.Char(readonly=True, help="Provider that handled the registration")
    gateway_document_id = fields.Char(
        readonly=True, copy=False, help="Resource id of this document inside the Integration Gateway"
    )
    provider_transaction_id = fields.Char(readonly=True, copy=False)
    irn = fields.Char(
        string="IRN", readonly=True, copy=False, index=True, help="Invoice Reference Number"
    )
    qr_payload = fields.Text(readonly=True, copy=False)
    qr_image_url = fields.Char(compute="_compute_qr_image_url")

    attempt_count = fields.Integer(readonly=True, default=0)
    last_error = fields.Text(readonly=True)
    correlation_id = fields.Char(readonly=True, copy=False)

    request_payload = fields.Text(readonly=True, help="Exact contract payload sent to the gateway")
    last_response = fields.Text(readonly=True)

    submitted_at = fields.Datetime(readonly=True)
    registered_at = fields.Datetime(readonly=True)
    cancelled_at = fields.Datetime(readonly=True)

    is_retryable = fields.Boolean(compute="_compute_is_retryable")

    _sql_constraints = [
        (
            "idempotency_key_unique",
            "unique(idempotency_key)",
            "A fiscal transaction with this idempotency key already exists.",
        ),
        (
            "source_unique",
            "unique(source_model, source_record_id, document_type)",
            "This document already has a fiscal transaction. "
            "Duplicate fiscal registration is not allowed.",
        ),
    ]

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends("qr_payload")
    def _compute_qr_image_url(self):
        """URL of Odoo's own barcode renderer - no extra dependency needed."""
        for record in self:
            if record.qr_payload:
                from urllib.parse import quote

                record.qr_image_url = (
                    "/report/barcode/?barcode_type=QR&value=%s&width=180&height=180"
                    % quote(record.qr_payload, safe="")
                )
            else:
                record.qr_image_url = False

    @api.depends("state", "attempt_count")
    def _compute_is_retryable(self):
        for record in self:
            record.is_retryable = record.state in RETRYABLE_STATES

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("et.fiscal.transaction") or _(
                    "New"
                )
            # Generated exactly once, here, and never touched again.
            vals.setdefault("idempotency_key", uuid.uuid4().hex)
            vals.setdefault("correlation_id", uuid.uuid4().hex)
        return super().create(vals_list)

    def write(self, vals):
        if "idempotency_key" in vals:
            for record in self:
                if record.idempotency_key and vals["idempotency_key"] != record.idempotency_key:
                    raise ValidationError(
                        _(
                            "The idempotency key of a fiscal transaction cannot be changed. "
                            "It is the identity that prevents duplicate registrations."
                        )
                    )
        return super().write(vals)

    def unlink(self):
        registered = self.filtered(lambda record: record.state == "registered")
        if registered and not self.env.context.get("force_fiscal_unlink"):
            raise UserError(
                _(
                    "Registered fiscal transactions cannot be deleted: %s. "
                    "They are the audit trail of a fiscal registration."
                )
                % ", ".join(registered.mapped("name"))
            )
        return super().unlink()

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------
    def _set_state(self, target):
        """Single entry point for every state change. Refuses invalid moves."""
        for record in self:
            allowed = ALLOWED_TRANSITIONS.get(record.state, set())
            if target not in allowed:
                raise ValidationError(
                    _("Cannot move fiscal transaction %(name)s from '%(current)s' to '%(target)s'.")
                    % {"name": record.name, "current": record.state, "target": target}
                )
            record.state = target

    # ------------------------------------------------------------------
    # Payload construction
    # ------------------------------------------------------------------
    def _get_source_record(self):
        self.ensure_one()
        if not self.source_model or self.source_model not in self.env:
            return None
        record = self.env[self.source_model].browse(self.source_record_id)
        return record if record.exists() else None

    def _build_payload(self):
        """Map the Odoo source record onto the normalized gateway contract.

        The source record implements ``_prepare_fiscal_document()`` through
        ``et.fiscal.document.mixin``; this method only adds the envelope that
        is identical for every document type.
        """
        self.ensure_one()
        source = self._get_source_record()
        if source is None:
            raise UserError(
                _("The source document of %s no longer exists (%s, id %s).")
                % (self.name, self.source_model, self.source_record_id)
            )

        document = source._prepare_fiscal_document()
        document["idempotency_key"] = self.idempotency_key
        document["source"] = {
            "system": "odoo",
            "model": self.source_model,
            "record_id": str(self.source_record_id),
            "reference": self.source_reference or "",
        }
        document.setdefault("document", {})["type"] = self.document_type
        return document

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------
    def action_submit(self):
        """Submit to the gateway. Safe to call repeatedly."""
        for record in self:
            record._submit()
        return True

    def action_retry(self):
        """Retry a failed submission under the original identity."""
        for record in self:
            if record.state not in RETRYABLE_STATES:
                raise UserError(
                    _("Fiscal transaction %s is '%s' and cannot be retried.")
                    % (record.name, record.state)
                )
            record._submit()
        return True

    def _submit(self):
        self.ensure_one()
        if self.state in TERMINAL_STATES:
            _logger.info(
                "fiscal transaction %s is already %s; not submitting again", self.name, self.state
            )
            return self

        if not self._fiscalization_enabled():
            _logger.info("fiscalization disabled; leaving %s pending", self.name)
            return self

        if self.state == "draft":
            self._set_state("pending")

        payload = self._build_payload()
        self._set_state("submitting")
        self.submitted_at = fields.Datetime.now()
        self.request_payload = json.dumps(payload, indent=2, default=str)

        client = self.env["et.fiscal.gateway.client"]
        try:
            response = client.register_fiscal_document(payload, correlation_id=self.correlation_id)
        except GatewayError as exc:
            # The gateway itself is unreachable. That is exactly the offline
            # case: stay retryable, keep the identity, try again later.
            self._record_failure(
                _("Integration Gateway unavailable: %s") % exc.message,
                response={"error": exc.message, "status_code": exc.status_code},
            )
            return self

        self._apply_gateway_response(response)
        return self

    def _apply_gateway_response(self, response):
        """Map a gateway document resource onto this record."""
        self.ensure_one()
        self.last_response = json.dumps(response, indent=2, default=str)
        self.gateway_document_id = response.get("id") or self.gateway_document_id
        self.provider = response.get("provider") or self.provider
        self.attempt_count = response.get("attempt_count", self.attempt_count)

        status = response.get("status")
        if status == "registered":
            self.provider_transaction_id = response.get("provider_transaction_id")
            self.irn = response.get("irn")
            self.qr_payload = response.get("qr_payload")
            self.registered_at = fields.Datetime.now()
            self.last_error = False
            if self.state != "registered":
                self._set_state("registered")
            self.message_post(
                body=_("Fiscal registration successful. IRN: %s (provider: %s)")
                % (self.irn or "-", self.provider or "-")
            )
            _logger.info(
                "fiscal transaction %s registered with IRN %s (idempotency_key=%s)",
                self.name,
                self.irn,
                self.idempotency_key,
            )
        elif status == "failed":
            self._record_failure(response.get("last_error") or _("provider rejected the document"))
        elif status in ("pending", "submitting"):
            # Gateway accepted it but has not finished; stay retryable.
            self._record_failure(
                _("Gateway reports status '%s'; will retry.") % status, keep_error_silent=True
            )
        elif status == "cancelled":
            if self.state != "cancelled":
                self._set_state("cancelled")
            self.cancelled_at = fields.Datetime.now()
        else:
            self._record_failure(_("Unexpected gateway status: %s") % status)

    def _record_failure(self, message, response=None, keep_error_silent=False):
        self.ensure_one()
        self.last_error = message
        if response is not None:
            self.last_response = json.dumps(response, indent=2, default=str)
        if self.state == "submitting":
            self._set_state("failed")
        if not keep_error_silent:
            self.message_post(body=_("Fiscal registration failed: %s") % message)
        _logger.warning(
            "fiscal transaction %s failed (idempotency_key=%s): %s",
            self.name,
            self.idempotency_key,
            message,
        )

    # ------------------------------------------------------------------
    # Reconciliation and recovery
    # ------------------------------------------------------------------
    def action_refresh_from_gateway(self):
        """Ask the gateway what it knows about this document.

        Used when Odoo lost the response but the gateway may already hold a
        registration. Looks up by idempotency key, which Odoo always has.
        """
        client = self.env["et.fiscal.gateway.client"]
        for record in self:
            try:
                if record.gateway_document_id:
                    response = client.get_fiscal_document(
                        record.gateway_document_id, correlation_id=record.correlation_id
                    )
                else:
                    response = client.get_fiscal_document_by_key(
                        record.idempotency_key, correlation_id=record.correlation_id
                    )
            except GatewayError as exc:
                if exc.status_code == 404:
                    _logger.info("gateway has no record of %s yet", record.name)
                    continue
                raise UserError(_("Could not reach the gateway: %s") % exc.message) from exc
            record._apply_gateway_response(response)
        return True

    def action_cancel(self):
        """Cancel a registered fiscal document at the provider."""
        client = self.env["et.fiscal.gateway.client"]
        for record in self:
            if record.state == "registered" and record.gateway_document_id:
                try:
                    response = client.cancel_fiscal_document(
                        record.gateway_document_id,
                        idempotency_key=f"cancel-{record.idempotency_key}",
                        reason=self.env.context.get("fiscal_cancel_reason", "cancelled in Odoo"),
                        correlation_id=record.correlation_id,
                    )
                except GatewayError as exc:
                    raise UserError(_("Could not cancel at the gateway: %s") % exc.message) from exc
                record._apply_gateway_response(response)
            else:
                record._set_state("cancelled")
                record.cancelled_at = fields.Datetime.now()
        return True

    def action_view_source(self):
        self.ensure_one()
        source = self._get_source_record()
        if source is None:
            raise UserError(_("The source document no longer exists."))
        return {
            "type": "ir.actions.act_window",
            "res_model": self.source_model,
            "res_id": self.source_record_id,
            "view_mode": "form",
            "target": "current",
        }

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------
    @api.model
    def _fiscalization_enabled(self):
        return (
            self.env["ir.config_parameter"].sudo().get_param("et_fiscal.enabled", "1")
        ).strip().lower() in ("1", "true", "yes", "on")

    @api.model
    def _auto_submit_enabled(self):
        return (
            self.env["ir.config_parameter"].sudo().get_param("et_fiscal.auto_submit", "1")
        ).strip().lower() in ("1", "true", "yes", "on")

    # ------------------------------------------------------------------
    # Cron: offline recovery
    # ------------------------------------------------------------------
    @api.model
    def cron_submit_pending(self, limit=50):
        """Push everything still waiting for a registration.

        This is the offline story: the shop keeps selling while the gateway or
        the provider is down, and this job drains the backlog afterwards. Each
        document carries its original idempotency key, so draining the backlog
        cannot double-register anything.
        """
        if not self._fiscalization_enabled():
            return 0

        params = self.env["ir.config_parameter"].sudo()
        try:
            max_attempts = int(params.get_param("et_fiscal.max_auto_retries") or 10)
        except (TypeError, ValueError):
            max_attempts = 10

        transactions = self.search(
            [
                ("state", "in", list(RETRYABLE_STATES)),
                ("attempt_count", "<", max_attempts),
            ],
            limit=limit,
            order="create_date asc",
        )
        _logger.info("fiscal cron: %s transaction(s) to submit", len(transactions))

        submitted = 0
        for transaction in transactions:
            try:
                transaction._submit()
                # Commit per document: one poisoned document must not block
                # the rest of the backlog.
                self.env.cr.commit()
                submitted += 1
            except Exception:  # noqa: BLE001 - a cron must never die on one record
                self.env.cr.rollback()
                _logger.exception("fiscal cron: failed to submit %s", transaction.name)
        return submitted
