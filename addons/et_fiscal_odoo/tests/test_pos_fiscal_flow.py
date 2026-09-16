"""POS order -> fiscal transaction: trigger, payload mapping, duplicate protection.

Uses Odoo's own POS test scaffolding so the orders are built the way the
point of sale really builds them.
"""

from unittest.mock import patch

from odoo.tests import tagged
from odoo.addons.point_of_sale.tests.common import TestPoSCommon


@tagged("post_install", "-at_install", "et_fiscal")
class TestPosFiscalFlow(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        cls.company_data["company"].et_fiscal_tin = "0012345678"
        params = cls.env["ir.config_parameter"].sudo()
        params.set_param("et_fiscal.gateway_base_url", "http://gateway.test:8000")
        params.set_param("et_fiscal.gateway_api_key", "test-key")
        # Creation is what we test here; submission is stubbed.
        params.set_param("et_fiscal.auto_submit", "0")
        cls.product = cls.create_product("Adidas Samba / Black / 42", cls.categ_basic, 6000.0)
        cls.product.write({"default_code": "SAM-BLK-42", "barcode": "2000000042012"})

    def _paid_order(self):
        self.open_new_session()
        order = self.create_ui_order_data([(self.product, 1)])
        results = self.env["pos.order"].sync_from_ui([order])
        order_id = results["pos.order"][0]["id"]
        return self.env["pos.order"].browse(order_id)

    def test_paid_order_gets_a_fiscal_transaction(self):
        order = self._paid_order()
        self.assertTrue(order.fiscal_transaction_id, "a paid POS order must be fiscalized")
        transaction = order.fiscal_transaction_id
        self.assertEqual(transaction.source_model, "pos.order")
        self.assertEqual(transaction.source_record_id, order.id)
        self.assertEqual(transaction.document_type, "receipt")
        self.assertEqual(transaction.state, "pending")
        self.assertAlmostEqual(transaction.amount_total, order.amount_total, places=2)

    def test_fiscal_transaction_is_created_once_per_order(self):
        """Double trigger protection - the unique constraint is the guarantee."""
        order = self._paid_order()
        first = order.fiscal_transaction_id

        order.action_pos_order_paid()
        order.invalidate_recordset()

        transactions = self.env["et.fiscal.transaction"].search(
            [("source_model", "=", "pos.order"), ("source_record_id", "=", order.id)]
        )
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions.id, first.id)

    def test_payload_matches_the_gateway_contract(self):
        order = self._paid_order()
        payload = order.fiscal_transaction_id._build_payload()

        # Envelope
        self.assertEqual(payload["idempotency_key"], order.fiscal_transaction_id.idempotency_key)
        self.assertEqual(payload["source"]["system"], "odoo")
        self.assertEqual(payload["source"]["model"], "pos.order")
        self.assertEqual(payload["source"]["record_id"], str(order.id))

        # Seller identity comes from the company, not from the module.
        self.assertEqual(payload["seller"]["tin"], "0012345678")

        # Lines carry the variant identity: SKU and barcode, not a product name.
        line = payload["lines"][0]
        self.assertEqual(line["sku"], "SAM-BLK-42")
        self.assertEqual(line["barcode"], "2000000042012")

        # Money is exchanged as 2dp strings, never floats.
        for key in ("subtotal", "tax_total", "grand_total"):
            self.assertIsInstance(payload["totals"][key], str)
            self.assertRegex(payload["totals"][key], r"^-?\d+\.\d{2}$")

        # Totals are internally consistent - the gateway rejects them otherwise.
        subtotal = float(payload["totals"]["subtotal"])
        tax = float(payload["totals"]["tax_total"])
        grand = float(payload["totals"]["grand_total"])
        self.assertAlmostEqual(subtotal + tax, grand, places=2)

        line_sum = sum(float(line["line_total"]) for line in payload["lines"])
        self.assertAlmostEqual(line_sum, subtotal, places=2)

    def test_payload_is_accepted_by_the_gateway_contract_schema(self):
        """Guards the Odoo->gateway contract from drifting.

        The same example lives in docs/contracts/ and is replayed against the
        running gateway by tests/contract/.
        """
        order = self._paid_order()
        payload = order.fiscal_transaction_id._build_payload()

        required_top_level = {
            "idempotency_key",
            "source",
            "document",
            "seller",
            "buyer",
            "lines",
            "taxes",
            "totals",
        }
        self.assertEqual(set(payload) & required_top_level, required_top_level)
        self.assertIn(payload["document"]["type"], ("receipt", "refund_receipt"))
        self.assertTrue(payload["document"]["number"])
        self.assertTrue(payload["lines"])

    def test_gateway_registration_is_written_back_to_the_order(self):
        order = self._paid_order()
        transaction = order.fiscal_transaction_id
        response = {
            "id": "gw-1",
            "status": "registered",
            "provider": "mock",
            "provider_transaction_id": "fp_00000009",
            "irn": "ET-DEMO-2026-000009",
            "qr_payload": "cXItcGF5bG9hZA==",
            "attempt_count": 1,
        }

        with patch.object(
            type(self.env["et.fiscal.gateway.client"]),
            "register_fiscal_document",
            return_value=response,
        ):
            transaction._submit()

        order.invalidate_recordset()
        self.assertEqual(order.fiscal_state, "registered")
        self.assertEqual(order.fiscal_irn, "ET-DEMO-2026-000009")
        self.assertTrue(order.fiscal_qr_payload)

    def test_a_provider_outage_never_blocks_the_sale(self):
        """The whole point of the boundary: stock moves, fiscalization waits."""
        from ..models.fiscal_gateway_client import GatewayError

        self.env["ir.config_parameter"].sudo().set_param("et_fiscal.auto_submit", "1")
        try:
            with patch.object(
                type(self.env["et.fiscal.gateway.client"]),
                "register_fiscal_document",
                side_effect=GatewayError("gateway down"),
            ):
                order = self._paid_order()
        finally:
            self.env["ir.config_parameter"].sudo().set_param("et_fiscal.auto_submit", "0")

        self.assertEqual(order.state, "paid")
        self.assertTrue(order.fiscal_transaction_id)
        self.assertEqual(order.fiscal_state, "failed")
        self.assertTrue(order.fiscal_transaction_id.is_retryable)
