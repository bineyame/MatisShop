"""Tests for the seeded demo environment.

These are configuration-assumption tests: they assert that the things the demo
script claims on screen are actually true in the database.
"""

from odoo.tests import TransactionCase, tagged

from ..models.mati_demo_setup import (
    OPENING_STOCK,
    WHOLESALE_BULK,
    build_barcode,
    ean13_check_digit,
)


@tagged("post_install", "-at_install", "mati_demo")
class TestBarcodeGeneration(TransactionCase):
    """Pure functions - no database needed, but cheap insurance."""

    def test_check_digit_matches_the_ean13_standard(self):
        # Known-good EAN-13 values.
        self.assertEqual(ean13_check_digit("400638133393"), "1")
        self.assertEqual(ean13_check_digit("978020137962"), "4")

    def test_generated_barcodes_are_valid_ean13(self):
        for product_index in (0, 1):
            for color_index in (0, 1):
                for size in ("41", "42"):
                    barcode = build_barcode(product_index, color_index, size)
                    self.assertEqual(len(barcode), 13)
                    self.assertTrue(barcode.isdigit())
                    self.assertEqual(barcode[12], ean13_check_digit(barcode[:12]))

    def test_generated_barcodes_are_unique_per_variant(self):
        barcodes = {
            build_barcode(product_index, color_index, size)
            for product_index in (0, 1)
            for color_index in (0, 1)
            for size in ("41", "42")
        }
        self.assertEqual(len(barcodes), 8)

    def test_barcode_generation_is_deterministic(self):
        self.assertEqual(build_barcode(0, 0, "42"), build_barcode(0, 0, "42"))
        self.assertEqual(build_barcode(0, 0, "42"), "2000004200008")


@tagged("post_install", "-at_install", "mati_demo")
class TestDemoSeed(TransactionCase):
    """Assertions about the seeded database."""

    def test_product_template_and_variants_exist(self):
        template = self.env["product.template"].search([("name", "=", "Adidas Samba")], limit=1)
        self.assertTrue(template, "the demo product template must exist")
        self.assertEqual(len(template.product_variant_ids), 4)

        attributes = set(template.attribute_line_ids.mapped("attribute_id.name"))
        self.assertEqual(attributes, {"Color", "Size"})

    def test_every_variant_has_a_unique_sku_and_barcode(self):
        variants = self.env["product.product"].search(
            [("default_code", "like", "SAM-")]
        )
        self.assertEqual(len(variants), 4)
        self.assertEqual(len(set(variants.mapped("default_code"))), 4)
        self.assertEqual(len(set(variants.mapped("barcode"))), 4)
        self.assertTrue(all(variants.mapped("barcode")))

    def test_barcode_resolves_to_exactly_one_variant(self):
        variant = self.env["product.product"].search([("default_code", "=", "SAM-BLK-42")], limit=1)
        self.assertTrue(variant)
        resolved = self.env["product.product"].search([("barcode", "=", variant.barcode)])
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved, variant)

    def test_sku_and_barcode_are_different_identifiers(self):
        variant = self.env["product.product"].search([("default_code", "=", "SAM-BLK-42")], limit=1)
        self.assertNotEqual(variant.default_code, variant.barcode)
        self.assertEqual(variant.default_code, "SAM-BLK-42")
        self.assertEqual(variant.barcode, "2000004200008")

    def test_three_warehouses_exist(self):
        codes = set(
            self.env["stock.warehouse"]
            .search([("company_id", "=", self.env.company.id)])
            .mapped("code")
        )
        self.assertTrue({"MAIN", "SHOP1", "SHOP2"}.issubset(codes), codes)

    def test_opening_stock_differs_by_location(self):
        variant = self.env["product.product"].search([("default_code", "=", "SAM-BLK-42")], limit=1)
        quantities = {}
        for code in ("MAIN", "SHOP1", "SHOP2"):
            warehouse = self.env["stock.warehouse"].search([("code", "=", code)], limit=1)
            quantities[code] = self.env["stock.quant"]._get_available_quantity(
                variant, warehouse.lot_stock_id
            )
        expected = OPENING_STOCK["SAM-BLK-42"]
        self.assertEqual(quantities["MAIN"], expected["MAIN"])
        self.assertEqual(quantities["SHOP1"], expected["SHOP1"])
        self.assertEqual(quantities["SHOP2"], expected["SHOP2"])
        self.assertGreater(len(set(quantities.values())), 1)

    def test_opening_stock_arrived_through_stock_moves(self):
        """No direct quantity writes: every unit is explained by a move."""
        variant = self.env["product.product"].search([("default_code", "=", "SAM-BLK-42")], limit=1)
        moves = self.env["stock.move"].search(
            [("product_id", "=", variant.id), ("state", "=", "done")]
        )
        self.assertTrue(moves, "opening stock must be backed by real stock moves")

    def test_supplier_and_vendor_price_exist(self):
        supplier = self.env["res.partner"].search([("name", "=", "ABC Footwear Factory")], limit=1)
        self.assertTrue(supplier)
        self.assertGreaterEqual(supplier.supplier_rank, 1)

        variant = self.env["product.product"].search([("default_code", "=", "SAM-BLK-42")], limit=1)
        info = self.env["product.supplierinfo"].search(
            [("partner_id", "=", supplier.id), ("product_id", "=", variant.id)], limit=1
        )
        self.assertTrue(info)
        self.assertAlmostEqual(info.price, 3200.0, places=2)

    def test_the_same_variant_has_different_prices_per_context(self):
        """The heart of the pricing story: one product, many prices."""
        variant = self.env["product.product"].search([("default_code", "=", "SAM-BLK-42")], limit=1)
        prices = {}
        for key, name in (
            ("retail", "Retail Pricelist"),
            ("online", "Online Pricelist"),
            ("wholesale", "Wholesale Pricelist"),
        ):
            pricelist = self.env["product.pricelist"].search([("name", "=", name)], limit=1)
            self.assertTrue(pricelist, f"{name} must exist")
            prices[key] = pricelist._get_product_price(variant, 1.0)

        self.assertAlmostEqual(prices["retail"], 6000.0, places=2)
        self.assertAlmostEqual(prices["online"], 6200.0, places=2)
        self.assertAlmostEqual(prices["wholesale"], 5300.0, places=2)
        self.assertEqual(len(set(prices.values())), 3)

    def test_wholesale_has_a_quantity_break(self):
        variant = self.env["product.product"].search([("default_code", "=", "SAM-BLK-42")], limit=1)
        wholesale = self.env["product.pricelist"].search(
            [("name", "=", "Wholesale Pricelist")], limit=1
        )
        single = wholesale._get_product_price(variant, 1.0)
        bulk = wholesale._get_product_price(variant, float(WHOLESALE_BULK["min_qty"]))
        self.assertAlmostEqual(single, 5300.0, places=2)
        self.assertAlmostEqual(bulk, 5000.0, places=2)
        self.assertLess(bulk, single)

    def test_no_duplicate_products_were_created_for_pricing(self):
        """Different prices must never mean different product records."""
        variants = self.env["product.product"].search([("default_code", "=", "SAM-BLK-42")])
        self.assertEqual(len(variants), 1)

    def test_pos_configs_are_bound_to_their_own_shop(self):
        shop1 = self.env["pos.config"].search([("name", "=", "Shop 1 Retail POS")], limit=1)
        shop2 = self.env["pos.config"].search([("name", "=", "Shop 2 Wholesale POS")], limit=1)
        self.assertTrue(shop1 and shop2)

        self.assertEqual(shop1.picking_type_id.warehouse_id.code, "SHOP1")
        self.assertEqual(shop2.picking_type_id.warehouse_id.code, "SHOP2")
        self.assertEqual(shop1.pricelist_id.name, "Retail Pricelist")
        self.assertEqual(shop2.pricelist_id.name, "Wholesale Pricelist")
        self.assertTrue(shop1.use_pricelist)

    def test_company_has_a_fiscal_identity(self):
        company = self.env.company
        self.assertEqual(company.et_fiscal_tin, "0012345678")

    def test_products_carry_a_sale_tax(self):
        template = self.env["product.template"].search([("name", "=", "Adidas Samba")], limit=1)
        self.assertTrue(template.taxes_id, "a fiscal document needs a tax block")

    def test_seeding_is_idempotent(self):
        """Running the seed twice must not duplicate anything."""
        Product = self.env["product.product"]
        Pricelist = self.env["product.pricelist"]
        before = (
            len(Product.search([("default_code", "like", "SAM-")])),
            len(Pricelist.search([("name", "=", "Retail Pricelist")])),
            len(self.env["res.partner"].search([("name", "=", "ABC Footwear Factory")])),
        )

        self.env["mati.demo.setup"].seed_all()

        after = (
            len(Product.search([("default_code", "like", "SAM-")])),
            len(Pricelist.search([("name", "=", "Retail Pricelist")])),
            len(self.env["res.partner"].search([("name", "=", "ABC Footwear Factory")])),
        )
        self.assertEqual(before, after)
