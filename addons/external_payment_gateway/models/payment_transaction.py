"""payment.transaction <-> Integration Gateway.

Odoo owns the transaction record and its state; the gateway owns talking to
the rail. The idempotency key is derived from the Odoo transaction reference,
so a retry - a user double-click, a network blip, a cron sweep - can never
charge a customer twice.
"""

import logging

from odoo import _, api, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Gateway status -> what Odoo's payment framework should do about it.
STATUS_HANDLERS = {
    "pending": "_set_pending",
    "authorized": "_set_authorized",
    "succeeded": "_set_done",
    "failed": "_set_error",
    "cancelled": "_set_canceled",
    "refunded": "_set_done",
    "partially_refunded": "_set_done",
}


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    def _gateway_idempotency_key(self):
        """Stable key for this payment.

        The Odoo reference is unique per transaction and never changes, which
        is exactly what an idempotency key needs to be.
        """
        self.ensure_one()
        return f"odoo-payment-{self.reference}"

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------
    def _send_payment_request(self):
        """Odoo 18 hook for a direct (non-redirect) payment request."""
        if self.provider_code != "integration_gateway":
            return super()._send_payment_request()
        self._gateway_create_payment()

    def _get_specific_rendering_values(self, processing_values):
        """Redirect flow: hand the customer the gateway checkout URL."""
        if self.provider_code != "integration_gateway":
            return super()._get_specific_rendering_values(processing_values)
        response = self._gateway_create_payment()
        return {
            "api_url": response.get("checkout_url") or "",
            "reference": self.reference,
        }

    def _gateway_create_payment(self):
        """Create (or replay) the payment at the gateway and apply the result."""
        self.ensure_one()
        payload = self._gateway_payment_payload()
        response = self.env["payment.gateway.client"].create_payment(
            self.provider_id, payload, correlation_id=self.reference
        )
        self._gateway_apply_response(response)
        return response

    def _gateway_payment_payload(self):
        self.ensure_one()
        partner = self.partner_id
        base_url = self.get_base_url()
        return {
            "idempotency_key": self._gateway_idempotency_key(),
            "reference": self.reference,
            "source": {
                "system": "odoo",
                "model": "payment.transaction",
                "record_id": str(self.id),
                "reference": self.reference,
            },
            "amount": f"{self.currency_id.round(self.amount):.2f}",
            "currency": self.currency_id.name,
            "customer": {
                "name": partner.name or "Customer",
                "phone": partner.phone or partner.mobile or None,
                "email": partner.email or None,
                "address": ", ".join(
                    part for part in [partner.street, partner.city, partner.country_id.name] if part
                )
                or None,
            },
            "description": self.reference,
            "return_url": f"{base_url}/payment/status",
            "cancel_url": f"{base_url}/payment/status",
            "metadata": {
                "odoo_transaction_id": self.id,
                "company": self.company_id.name,
            },
        }

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------
    def _gateway_apply_response(self, response):
        """Map a gateway payment resource onto Odoo's transaction state."""
        self.ensure_one()
        status = response.get("status")
        provider_reference = response.get("provider_transaction_id")
        if provider_reference:
            self.provider_reference = provider_reference

        handler = STATUS_HANDLERS.get(status)
        if handler is None:
            _logger.warning(
                "unexpected gateway payment status '%s' for %s", status, self.reference
            )
            self._set_error(_("Unexpected payment status from the gateway: %s") % status)
            return

        if status == "failed":
            self._set_error(response.get("last_error") or _("payment was declined"))
        else:
            getattr(self, handler)()

        _logger.info(
            "payment %s -> gateway status %s (provider_reference=%s)",
            self.reference,
            status,
            provider_reference,
        )

    def action_gateway_poll_status(self):
        """Ask the gateway for authoritative state.

        The reference flow is polling rather than an inbound webhook: it needs
        no public network path into Odoo, which matters for a shop running
        behind a home router.
        """
        for transaction in self:
            if transaction.provider_code != "integration_gateway":
                continue
            response = self.env["payment.gateway.client"].get_payment_by_reference(
                transaction.provider_id, transaction.reference
            )
            transaction._gateway_apply_response(response)
        return True

    @api.model
    def _get_tx_from_notification_data(self, provider_code, notification_data):
        """Resolve the Odoo transaction a gateway callback refers to."""
        if provider_code != "integration_gateway":
            return super()._get_tx_from_notification_data(provider_code, notification_data)

        reference = notification_data.get("reference")
        if not reference:
            raise ValidationError(_("Gateway notification is missing the transaction reference."))
        transaction = self.search(
            [("reference", "=", reference), ("provider_code", "=", "integration_gateway")], limit=1
        )
        if not transaction:
            raise ValidationError(
                _("No Odoo payment transaction matches reference %s.") % reference
            )
        return transaction

    def _process_notification_data(self, notification_data):
        if self.provider_code != "integration_gateway":
            return super()._process_notification_data(notification_data)
        # Never trust the notification body for money: re-read the
        # authoritative state from the gateway before changing anything.
        self.action_gateway_poll_status()

    # ------------------------------------------------------------------
    # Refunds
    # ------------------------------------------------------------------
    def _send_refund_request(self, amount_to_refund=None):
        """Refund through the gateway.

        Odoo's base implementation creates a CHILD transaction and returns it;
        the refund's own state belongs on that child, not on the source
        transaction, which is already `done`.
        """
        if self.provider_code != "integration_gateway":
            return super()._send_refund_request(amount_to_refund=amount_to_refund)

        refund_tx = super()._send_refund_request(amount_to_refund=amount_to_refund)

        client = self.env["payment.gateway.client"]
        gateway_payment = client.get_payment_by_reference(self.provider_id, self.reference)
        response = client.refund_payment(
            self.provider_id,
            gateway_payment["id"],
            {
                # Keyed on the refund transaction, so retrying a refund cannot
                # refund twice.
                "idempotency_key": f"odoo-refund-{refund_tx.reference}",
                "amount": f"{abs(refund_tx.currency_id.round(refund_tx.amount)):.2f}",
                "reason": refund_tx.reference,
            },
        )

        if response.get("status") in ("refunded", "partially_refunded"):
            refund_tx.provider_reference = response.get("provider_transaction_id")
            refund_tx._set_done()
        else:
            refund_tx._set_error(response.get("last_error") or _("refund was rejected"))
        return refund_tx
