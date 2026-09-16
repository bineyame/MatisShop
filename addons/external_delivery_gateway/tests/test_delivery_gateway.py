"""Delivery connector tests: payload mapping, booking, status refresh."""

from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "external_delivery_gateway")
class TestDeliveryGatewayConnector(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env.ref(
            "external_delivery_gateway.delivery_carrier_integration_gateway"
        )
        cls.carrier.write({
            "gateway_base_url": "http://gateway.test:8000",
            "gateway_api_key": "test-key",
        })
        cls.customer = cls.env["res.partner"].create({
            "name": "Abebe Kebede",
            "street": "Bole Road",
            "city": "Addis Ababa",
            "phone": "+251911223344",
        })
        cls.product = cls.env["product.product"].create({
            "name": "Adidas Samba / Black / 42",
            "default_code": "SAM-BLK-42",
            "is_storable": True,
        })

    def _picking(self):
        picking_type = self.env.ref("stock.picking_type_out")
        picking = self.env["stock.picking"].create({
            "picking_type_id": picking_type.id,
            "partner_id": self.customer.id,
            "location_id": picking_type.default_location_src_id.id,
            "location_dest_id": self.env.ref("stock.stock_location_customers").id,
            "carrier_id": self.carrier.id,
        })
        self.env["stock.move"].create({
            "name": self.product.name,
            "product_id": self.product.id,
            "product_uom_qty": 1,
            "product_uom": self.product.uom_id.id,
            "picking_id": picking.id,
            "location_id": picking.location_id.id,
            "location_dest_id": picking.location_dest_id.id,
        })
        return picking

    def test_payload_matches_the_delivery_contract(self):
        picking = self._picking()
        payload = picking._gateway_delivery_payload()

        self.assertEqual(payload["idempotency_key"], f"odoo-delivery-{picking.name}")
        self.assertEqual(payload["reference"], picking.name)
        self.assertEqual(payload["source"]["model"], "stock.picking")
        self.assertEqual(payload["dropoff"]["name"], "Abebe Kebede")
        self.assertEqual(payload["dropoff"]["city"], "Addis Ababa")
        self.assertEqual(len(payload["packages"]), 1)
        self.assertEqual(payload["packages"][0]["sku"], "SAM-BLK-42")

    def test_idempotency_key_is_stable(self):
        picking = self._picking()
        first = picking._gateway_delivery_payload()["idempotency_key"]
        second = picking._gateway_delivery_payload()["idempotency_key"]
        self.assertEqual(first, second)

    def test_booking_stores_the_courier_reference(self):
        picking = self._picking()
        response = {
            "id": "gw-del-1",
            "status": "created",
            "provider": "mock",
            "tracking_number": "MD1234567890",
            "tracking_url": "https://mock-delivery.local/track/MD1234567890",
            "price": "120.00",
        }
        with patch.object(type(self.carrier), "_gateway_request", return_value=response):
            result = self.carrier.integration_gateway_send_shipping(picking)

        self.assertEqual(picking.gateway_delivery_id, "gw-del-1")
        self.assertEqual(picking.gateway_delivery_status, "created")
        self.assertEqual(picking.gateway_delivery_provider, "mock")
        self.assertEqual(picking.carrier_tracking_ref, "MD1234567890")
        self.assertEqual(result[0]["exact_price"], 120.0)

    def test_status_refresh_updates_the_picking(self):
        picking = self._picking()
        picking.gateway_delivery_id = "gw-del-1"
        with patch.object(
            type(self.carrier),
            "_gateway_request",
            return_value={"id": "gw-del-1", "status": "in_transit"},
        ):
            picking.action_refresh_delivery_status()
        self.assertEqual(picking.gateway_delivery_status, "in_transit")

    def test_rate_shipment_returns_a_demo_quote(self):
        order = self.env["sale.order"].create({"partner_id": self.customer.id})
        self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": self.product.id,
            "product_uom_qty": 1,
        })
        rate = self.carrier.integration_gateway_rate_shipment(order)
        self.assertTrue(rate["success"])
        self.assertEqual(rate["price"], 120.0)
        self.assertIn("DEMO", rate["warning_message"])
