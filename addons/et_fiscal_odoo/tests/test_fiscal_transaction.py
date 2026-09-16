"""Odoo-side fiscal tests.

The gateway is stubbed: these tests are about Odoo's half of the contract -
transaction creation, source linkage, payload mapping, state transitions,
duplicate protection and how gateway responses are applied. The gateway's own
behaviour is tested in gateway/tests.

Run with:
    docker compose run --rm odoo odoo -d odoo --test-enable \\
        --test-tags /et_fiscal_odoo --stop-after-init
"""

import json
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.addons.point_of_sale.tests.common import TestPoSCommon

from ..models.fiscal_gateway_client import GatewayError

REGISTERED_RESPONSE = {
    "id": "gw-doc-1",
    "idempotency_key": None,  # filled in per test
    "status": "registered",
    "provider": "mock",
    "provider_transaction_id": "fp_00000001",
    "irn": "ET-DEMO-2026-000001",
    "qr_payload": "RVREUUVNTzF8RVQtREVNTw==",
    "attempt_count": 1,
    "grand_total": "6900.00",
    "registered_at": "2026-01-01T10:00:00+00:00",
    "replayed": False,
}

FAILED_RESPONSE = {
    "id": "gw-doc-1",
    "status": "failed",
    "provider": "mock",
    "irn": None,
    "qr_payload": None,
    "attempt_count": 3,
    "last_error": "mock fiscal provider is in forced failure mode",
    "replayed": False,
}


@tagged("post_install", "-at_install", "et_fiscal")
class TestFiscalTransaction(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_data["company"].et_fiscal_tin = "0012345678"
        cls.env["ir.config_parameter"].sudo().set_param(
            "et_fiscal.gateway_base_url", "http://gateway.test:8000"
        )
        cls.env["ir.config_parameter"].sudo().set_param("et_fiscal.gateway_api_key", "test-key")
        # Auto-submit off: these tests drive submission explicitly.
        cls.env["ir.config_parameter"].sudo().set_param("et_fiscal.auto_submit", "0")

    def _make_transaction(self, **overrides):
        values = {
            "source_model": "pos.order",
            "source_record_id": 1,
            "source_reference": "Shop 1 Retail/0001",
            "document_type": "receipt",
            "company_id": self.env.company.id,
            "currency_id": self.env.company.currency_id.id,
            "amount_untaxed": 6000.0,
            "amount_tax": 900.0,
            "amount_total": 6900.0,
            "state": "pending",
        }
        values.update(overrides)
        return self.env["et.fiscal.transaction"].create(values)

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    def test_idempotency_key_is_generated_once(self):
        transaction = self._make_transaction()
        self.assertTrue(transaction.idempotency_key)
        self.assertEqual(len(transaction.idempotency_key), 32)

    def test_idempotency_key_cannot_be_changed(self):
        transaction = self._make_transaction()
        with self.assertRaises(ValidationError):
            transaction.write({"idempotency_key": "something-else"})

    def test_idempotency_key_is_unique(self):
        first = self._make_transaction()
        with self.assertRaises(Exception):
            self._make_transaction(
                source_record_id=2, idempotency_key=first.idempotency_key
            ).flush_recordset()

    def test_sequence_is_assigned(self):
        transaction = self._make_transaction()
        self.assertNotEqual(transaction.name, "New")
        self.assertIn("FT/", transaction.name)

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------
    def test_valid_transition_is_allowed(self):
        transaction = self._make_transaction(state="pending")
        transaction._set_state("submitting")
        self.assertEqual(transaction.state, "submitting")
        transaction._set_state("registered")
        self.assertEqual(transaction.state, "registered")

    def test_registered_cannot_go_back_to_pending(self):
        transaction = self._make_transaction(state="pending")
        transaction._set_state("submitting")
        transaction._set_state("registered")
        with self.assertRaises(ValidationError):
            transaction._set_state("pending")

    def test_pending_cannot_jump_straight_to_registered(self):
        transaction = self._make_transaction(state="pending")
        with self.assertRaises(ValidationError):
            transaction._set_state("registered")

    def test_failed_can_be_resubmitted(self):
        transaction = self._make_transaction(state="pending")
        transaction._set_state("submitting")
        transaction._set_state("failed")
        transaction._set_state("submitting")
        self.assertEqual(transaction.state, "submitting")

    def test_registered_transaction_cannot_be_deleted(self):
        transaction = self._make_transaction(state="pending")
        transaction._set_state("submitting")
        transaction._set_state("registered")
        with self.assertRaises(Exception):
            transaction.unlink()

    # ------------------------------------------------------------------
    # Gateway response mapping
    # ------------------------------------------------------------------
    def test_registered_response_is_stored(self):
        transaction = self._make_transaction(state="pending")
        transaction._set_state("submitting")
        response = dict(REGISTERED_RESPONSE, idempotency_key=transaction.idempotency_key)

        transaction._apply_gateway_response(response)

        self.assertEqual(transaction.state, "registered")
        self.assertEqual(transaction.irn, "ET-DEMO-2026-000001")
        self.assertEqual(transaction.provider, "mock")
        self.assertEqual(transaction.provider_transaction_id, "fp_00000001")
        self.assertEqual(transaction.gateway_document_id, "gw-doc-1")
        self.assertTrue(transaction.qr_payload)
        self.assertTrue(transaction.registered_at)
        self.assertFalse(transaction.last_error)

    def test_failed_response_keeps_the_document_retryable(self):
        transaction = self._make_transaction(state="pending")
        transaction._set_state("submitting")

        transaction._apply_gateway_response(FAILED_RESPONSE)

        self.assertEqual(transaction.state, "failed")
        self.assertTrue(transaction.is_retryable)
        self.assertFalse(transaction.irn)
        self.assertIn("failure mode", transaction.last_error)
        self.assertEqual(transaction.attempt_count, 3)

    def test_unreachable_gateway_is_a_retryable_failure(self):
        """The offline case: the sale stands, the registration waits."""
        transaction = self._make_transaction(state="pending")

        with patch.object(
            type(self.env["et.fiscal.gateway.client"]),
            "register_fiscal_document",
            side_effect=GatewayError("connection refused"),
        ), patch.object(
            type(transaction), "_build_payload", return_value={"idempotency_key": "x"}
        ):
            transaction._submit()

        self.assertEqual(transaction.state, "failed")
        self.assertTrue(transaction.is_retryable)
        self.assertIn("unavailable", transaction.last_error)

    def test_retry_reuses_the_same_identity(self):
        transaction = self._make_transaction(state="pending")
        original_key = transaction.idempotency_key
        transaction._set_state("submitting")
        transaction._apply_gateway_response(FAILED_RESPONSE)

        response = dict(REGISTERED_RESPONSE, idempotency_key=original_key)
        with patch.object(
            type(self.env["et.fiscal.gateway.client"]),
            "register_fiscal_document",
            return_value=response,
        ) as mocked, patch.object(
            type(transaction), "_build_payload", return_value={"idempotency_key": original_key}
        ):
            transaction.action_retry()

        self.assertEqual(transaction.state, "registered")
        self.assertEqual(transaction.idempotency_key, original_key)
        sent_payload = mocked.call_args[0][0]
        self.assertEqual(sent_payload["idempotency_key"], original_key)

    def test_already_registered_transaction_is_not_resubmitted(self):
        transaction = self._make_transaction(state="pending")
        transaction._set_state("submitting")
        transaction._apply_gateway_response(
            dict(REGISTERED_RESPONSE, idempotency_key=transaction.idempotency_key)
        )

        with patch.object(
            type(self.env["et.fiscal.gateway.client"]), "register_fiscal_document"
        ) as mocked:
            transaction._submit()

        mocked.assert_not_called()
        self.assertEqual(transaction.state, "registered")

    def test_unexpected_status_is_treated_as_a_failure(self):
        transaction = self._make_transaction(state="pending")
        transaction._set_state("submitting")
        transaction._apply_gateway_response({"status": "who_knows", "id": "gw-1"})
        self.assertEqual(transaction.state, "failed")
        self.assertIn("Unexpected", transaction.last_error)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    def test_disabling_fiscalization_stops_submission(self):
        self.env["ir.config_parameter"].sudo().set_param("et_fiscal.enabled", "0")
        transaction = self._make_transaction(state="pending")

        with patch.object(
            type(self.env["et.fiscal.gateway.client"]), "register_fiscal_document"
        ) as mocked:
            transaction._submit()

        mocked.assert_not_called()
        self.assertEqual(transaction.state, "pending")
        self.env["ir.config_parameter"].sudo().set_param("et_fiscal.enabled", "1")

    def test_cron_only_picks_up_retryable_documents(self):
        pending = self._make_transaction(state="pending", source_record_id=101)
        registered = self._make_transaction(state="pending", source_record_id=102)
        registered._set_state("submitting")
        registered._apply_gateway_response(
            dict(REGISTERED_RESPONSE, idempotency_key=registered.idempotency_key)
        )

        submitted = []
        original_submit = type(pending)._submit

        def _spy(self):
            submitted.append(self.id)
            return original_submit(self)

        with patch.object(type(pending), "_submit", _spy), patch.object(
            type(self.env["et.fiscal.gateway.client"]),
            "register_fiscal_document",
            side_effect=GatewayError("still down"),
        ), patch.object(type(pending), "_build_payload", return_value={"idempotency_key": "x"}):
            self.env["et.fiscal.transaction"].cron_submit_pending()

        self.assertIn(pending.id, submitted)
        self.assertNotIn(registered.id, submitted)


@tagged("post_install", "-at_install", "et_fiscal")
class TestFiscalGatewayClient(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        params = cls.env["ir.config_parameter"].sudo()
        params.set_param("et_fiscal.gateway_base_url", "http://gateway.test:8000")
        params.set_param("et_fiscal.gateway_api_key", "test-key")

    def test_api_key_is_sent_in_the_header(self):
        client = self.env["et.fiscal.gateway.client"]
        config = client._get_config()
        headers = client._headers(config, correlation_id="corr-1")
        self.assertEqual(headers["X-API-Key"], "test-key")
        self.assertEqual(headers["X-Correlation-Id"], "corr-1")

    def test_http_error_becomes_a_gateway_error(self):
        class FakeResponse:
            status_code = 503
            text = "unavailable"

            @staticmethod
            def json():
                return {"code": "provider_unavailable", "message": "provider is down"}

        with patch("requests.request", return_value=FakeResponse()):
            with self.assertRaises(GatewayError) as ctx:
                self.env["et.fiscal.gateway.client"].register_fiscal_document({"a": 1})

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("provider is down", ctx.exception.message)

    def test_successful_response_is_returned_as_a_dict(self):
        class FakeResponse:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                return dict(REGISTERED_RESPONSE)

        with patch("requests.request", return_value=FakeResponse()) as mocked:
            result = self.env["et.fiscal.gateway.client"].register_fiscal_document(
                {"idempotency_key": "abc"}, correlation_id="corr-2"
            )

        self.assertEqual(result["irn"], "ET-DEMO-2026-000001")
        called_kwargs = mocked.call_args.kwargs
        self.assertEqual(json.loads(called_kwargs["data"])["idempotency_key"], "abc")
        self.assertEqual(called_kwargs["headers"]["X-Correlation-Id"], "corr-2")
