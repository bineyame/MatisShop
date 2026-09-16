"""Thin HTTP client for the Integration Gateway.

This is the ONLY place in Odoo that knows the gateway speaks HTTP. It knows
nothing about any fiscal provider: no provider URL, no provider credential, no
provider payload shape. Swapping the mock provider for a real accredited one
changes nothing here (ADR-005).

Deliberately kept small:
  * no retry engine  - the gateway owns retries; Odoo owns "try again later"
  * no signing       - the gateway owns provider signing
  * no secrets       - the API key comes from ir.config_parameter, itself
                       seeded from the environment
"""

import json
import logging

import requests

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 20


class GatewayError(Exception):
    """Transport-level failure talking to the gateway.

    A *provider* failure is not this: the gateway reports that as a normal
    response with ``status == "failed"``.
    """

    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload or {}


class FiscalGatewayClient(models.AbstractModel):
    _name = "et.fiscal.gateway.client"
    _description = "Integration Gateway HTTP client"

    # -- configuration -------------------------------------------------------
    @api.model
    def _get_config(self):
        params = self.env["ir.config_parameter"].sudo()
        base_url = (params.get_param("et_fiscal.gateway_base_url") or "").rstrip("/")
        if not base_url:
            raise UserError(
                _(
                    "No Integration Gateway URL configured. Set it under "
                    "Settings > Ethiopian Fiscalization."
                )
            )
        try:
            timeout = float(params.get_param("et_fiscal.gateway_timeout") or DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        return {
            "base_url": base_url,
            "api_key": params.get_param("et_fiscal.gateway_api_key") or "",
            "timeout": timeout,
        }

    @api.model
    def _headers(self, config, correlation_id=None):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-Key": config["api_key"],
        }
        if correlation_id:
            headers["X-Correlation-Id"] = correlation_id
        return headers

    # -- transport -----------------------------------------------------------
    @api.model
    def _request(self, method, path, payload=None, correlation_id=None):
        config = self._get_config()
        url = f"{config['base_url']}{path}"
        headers = self._headers(config, correlation_id)

        # Never log the API key, and never log a full payload at info level.
        _logger.info(
            "gateway request %s %s (correlation_id=%s)", method, path, correlation_id or "-"
        )
        try:
            response = requests.request(
                method,
                url,
                data=json.dumps(payload) if payload is not None else None,
                headers=headers,
                timeout=config["timeout"],
            )
        except requests.exceptions.Timeout as exc:
            raise GatewayError(
                _("Integration Gateway timed out after %ss") % config["timeout"]
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise GatewayError(
                _("Integration Gateway is unreachable: %s") % exc
            ) from exc

        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.status_code >= 400:
            message = body.get("message") or response.text[:500] or _("unknown gateway error")
            _logger.warning(
                "gateway error %s %s -> HTTP %s: %s",
                method,
                path,
                response.status_code,
                message,
            )
            raise GatewayError(message, status_code=response.status_code, payload=body)

        if not isinstance(body, dict):
            raise GatewayError(_("Integration Gateway returned a malformed response"))
        return body

    # -- fiscal rail ---------------------------------------------------------
    @api.model
    def register_fiscal_document(self, payload, correlation_id=None):
        """POST /api/v1/fiscal/documents

        Returns the gateway document resource. A provider failure comes back
        as a normal dict with ``status == "failed"``; only transport problems
        raise ``GatewayError``.
        """
        return self._request(
            "POST", "/api/v1/fiscal/documents", payload, correlation_id=correlation_id
        )

    @api.model
    def retry_fiscal_document(self, document_id, correlation_id=None):
        return self._request(
            "POST",
            f"/api/v1/fiscal/documents/{document_id}/retry",
            correlation_id=correlation_id,
        )

    @api.model
    def get_fiscal_document(self, document_id, correlation_id=None):
        return self._request(
            "GET", f"/api/v1/fiscal/documents/{document_id}", correlation_id=correlation_id
        )

    @api.model
    def get_fiscal_document_by_key(self, idempotency_key, correlation_id=None):
        """Recovery path: Odoo kept its key but lost the gateway id."""
        return self._request(
            "GET",
            f"/api/v1/fiscal/documents/by-key/{idempotency_key}",
            correlation_id=correlation_id,
        )

    @api.model
    def cancel_fiscal_document(self, document_id, idempotency_key, reason, correlation_id=None):
        return self._request(
            "POST",
            f"/api/v1/fiscal/documents/{document_id}/cancel",
            {"idempotency_key": idempotency_key, "reason": reason},
            correlation_id=correlation_id,
        )

    @api.model
    def health(self):
        return self._request("GET", "/health")
