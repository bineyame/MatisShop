"""Payment connector tests: identity, payload mapping, status mapping."""

from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "external_payment_gateway")
class TestPaymentGatewayConnector(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("external_payment_gateway.payment_provider_integration_gateway")
        cls.provider.write({
            "gateway_base_url": "http://gateway.test:8000",
            "gateway_api_key": "test-key",
            "state": "test",
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "Abebe Kebede",
            "phone": "+251911223344",
            "email": "abebe@example.com",
        })
        cls.currency = cls.env.company.currency_id

    def _transaction(self, amount=6900.0):
        return self.env["payment.transaction"].create({
            "provider_id": self.provider.id,
            "payment_method_id": self.provider.payment_method_ids[:1].id,
            "reference": self.env["payment.transaction"]._compute_reference(
                self.provider.code, prefix="TEST"
            ),
            "amount": amount,
            "currency_id": self.currency.id,
            "partner_id": self.partner.id,
        })

    def test_idempotency_key_is_derived_from_the_reference(self):
        transaction = self._transaction()
        key = transaction._gateway_idempotency_key()
        self.assertEqual(key, f"odoo-payment-{transaction.reference}")
        # Stable across calls: this is what prevents double charges.
        self.assertEqual(key, transaction._gateway_idempotency_key())

    def test_payload_matches_the_gateway_contract(self):
        transaction = self._transaction()
        payload = transaction._gateway_payment_payload()

        self.assertEqual(payload["reference"], transaction.reference)
        self.assertEqual(payload["source"]["model"], "payment.transaction")
        self.assertEqual(payload["currency"], self.currency.name)
        self.assertRegex(payload["amount"], r"^\d+\.\d{2}$")
        self.assertEqual(payload["customer"]["name"], "Abebe Kebede")
        self.assertEqual(payload["idempotency_key"], transaction._gateway_idempotency_key())

    def test_successful_payment_sets_the_transaction_done(self):
        transaction = self._transaction()
        response = {
            "id": "gw-pay-1",
            "status": "succeeded",
            "provider": "mock",
            "provider_transaction_id": "mp_abc123",
            "amount": "6900.00",
        }
        with patch.object(
            type(self.env["payment.gateway.client"]), "create_payment", return_value=response
        ):
            transaction._gateway_create_payment()

        self.assertEqual(transaction.state, "done")
        self.assertEqual(transaction.provider_reference, "mp_abc123")

    def test_failed_payment_sets_an_error(self):
        transaction = self._transaction()
        response = {
            "id": "gw-pay-2",
            "status": "failed",
            "provider": "mock",
            "last_error": "mock payment provider rejected the payment",
        }
        with patch.object(
            type(self.env["payment.gateway.client"]), "create_payment", return_value=response
        ):
            transaction._gateway_create_payment()

        self.assertEqual(transaction.state, "error")
        self.assertIn("rejected", transaction.state_message or "")

    def test_pending_payment_stays_pending(self):
        transaction = self._transaction()
        response = {
            "id": "gw-pay-3",
            "status": "pending",
            "provider": "mock",
            "provider_transaction_id": "mp_pending",
            "checkout_url": "https://mock-payments.local/checkout/mp_pending",
        }
        with patch.object(
            type(self.env["payment.gateway.client"]), "create_payment", return_value=response
        ):
            values = transaction._get_specific_rendering_values({})

        self.assertEqual(transaction.state, "pending")
        self.assertEqual(values["api_url"], "https://mock-payments.local/checkout/mp_pending")

    def test_unknown_status_is_an_error_not_a_silent_success(self):
        transaction = self._transaction()
        with patch.object(
            type(self.env["payment.gateway.client"]),
            "create_payment",
            return_value={"id": "x", "status": "banana"},
        ):
            transaction._gateway_create_payment()
        self.assertEqual(transaction.state, "error")

    def test_provider_falls_back_to_the_fiscal_gateway_settings(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "et_fiscal.gateway_base_url", "http://fallback:8000"
        )
        self.provider.write({"gateway_base_url": False, "gateway_api_key": False})
        settings = self.provider._gateway_settings()
        self.assertEqual(settings["base_url"], "http://fallback:8000")
