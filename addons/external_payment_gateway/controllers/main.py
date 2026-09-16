"""Gateway -> Odoo notification endpoint.

Optional. The reference flow is Odoo polling the gateway, which needs no
inbound network path into Odoo at all. This endpoint exists for deployments
that do want push notifications.

Security notes:
  * ``auth="public"`` is unavoidable for a callback, so the body is NEVER
    trusted: it only tells Odoo which transaction to re-read.
  * the authoritative state is then fetched from the gateway over an
    authenticated call. A forged callback can therefore cause a pointless
    lookup, never a wrong payment state.
"""

import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class PaymentGatewayController(http.Controller):
    @http.route(
        "/payment/integration_gateway/notification",
        type="json",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def gateway_notification(self, **kwargs):
        data = request.get_json_data() or {}
        reference = data.get("reference")
        _logger.info("gateway payment notification for reference %s", reference)
        if not reference:
            return {"status": "ignored", "reason": "missing reference"}

        transaction = (
            request.env["payment.transaction"]
            .sudo()
            .search(
                [("reference", "=", reference), ("provider_code", "=", "integration_gateway")],
                limit=1,
            )
        )
        if not transaction:
            return {"status": "ignored", "reason": "unknown reference"}

        # Re-read authoritative state rather than believing the payload.
        transaction.action_gateway_poll_status()
        return {"status": "ok", "state": transaction.state}
