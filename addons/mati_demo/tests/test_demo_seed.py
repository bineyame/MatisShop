"""Tests for the seeded demo environment.

Configuration-assumption tests: they assert that the things the demo script
says out loud are actually true in the database.

These run against a database that may already have had a demo walked through
it, so anything depending on pristine stock re-applies the opening state
first rather than assuming nobody has sold anything.
"""

import itertools

from odoo.tests import TransactionCase, tagged

from ..models.mati_demo_data import (
    DEMO_PURCHASE,
    OPENING_STOCK,
    PRODUCTS,
    build_barcode,
    build_sku,
    ean13_check_digit,
    iter_variants,
)


@tagged("post_install", "-at_install", "mati_demo")
class TestDemoDataset(TransactionCase):
    """Pure functions and dataset consistency - no database needed."""

    def test_check_digit_matches_the_ean13_standard(self):
        self.assertEqual(ean13_check_digit("400638133393"), "1")
        self.assertEqual(ean13_check_digit("978020137962"), "4")

    def test_generated_barcodes_are_valid_ean13(self):
        for *_, barcode in ((v[0], v[5]) for v in iter_variants()):
            self.assertEqual(len(barcode), 13)
            self.assertTrue(barcode.isdigit())
            self.assertEqual(barcode[12], ean13_check_digit(barcode[:12]))

    def test_barcodes_and_skus_are_unique(self):
        rows = list(iter_variants())
        self.assertEqual(len({r[5] for r in rows}), len(rows), "barcode collision")
        self.assertEqual(len({r[4] for r in rows}), len(rows), "SKU collision")

    def test_barcode_generation_is_deterministic(self):
        self.assertEqual(build_barcode(0, 0, "39"), build_barcode(0, 0, "39"))
        self.assertEqual(build_sku(PRODUCTS["zala2147"], "Black", "39"), "ZALA-2147-BLK-39")

    def test_opening_stock_covers_every_variant(self):
        self.assertEqual(set(OPENING_STOCK), {row[4] for row in iter_variants()})

    def test_the_purchase_scenario_is_arithmetically_consistent(self):
        scenario = DEMO_PURCHASE
        self.assertEqual(
            OPENING_STOCK[scenario["sku"]][scenario["shop"]], scenario["opening"]
        )
        self.assertEqual(
            scenario["opening"] + scenario["quantity"], scenario["after_receipt"]
        )


@tagged("post_install", "-at_install", "mati_demo")
class TestDemoSeed(TransactionCase):
    """Assertions about the seeded database."""

    def _variant(self, sku=None):
        return self.env["product.product"].search(
            [("default_code", "=", sku or DEMO_PURCHASE["sku"])], limit=1
        )

    def _shop(self, code):
        return self.env["stock.warehouse"].search([("code", "=", code)], limit=1)

    def _qty(self, variant, code):
        return self.env["stock.quant"]._get_available_quantity(
            variant, self._shop(code).lot_stock_id
        )

    def _reset_opening_stock(self):
        shops = {
            warehouse.code: warehouse
            for warehouse in self.env["stock.warehouse"].search(
                [("company_id", "=", self.env.company.id)]
            )
        }
        self.env["mati.demo.setup"]._seed_opening_stock(self.env.company, shops)

    # -- Legal structure ------------------------------------------------
    def test_one_company_one_tin(self):
        """Two shops, one legal entity. Multi-company is deliberately NOT used."""
        self.assertEqual(self.env["res.company"].search_count([]), 1)
        self.assertEqual(self.env.company.et_fiscal_tin, "0012345678")

    # -- Catalogue ------------------------------------------------------
    def test_factory_and_model_identify_the_template(self):
        template = self.env["product.template"].search([("name", "=", "Zala 2147")], limit=1)
        self.assertTrue(template)
        self.assertEqual(template.shoe_factory, "Zala")
        self.assertEqual(template.shoe_model, "2147")
        self.assertEqual(template.shoe_reference, "Zala 2147")

    def test_colour_and_size_are_the_variant_dimensions(self):
        template = self.env["product.template"].search([("name", "=", "Zala 2147")], limit=1)
        attributes = set(template.attribute_line_ids.mapped("attribute_id.name"))
        self.assertEqual(attributes, {"Color", "Size"})
        self.assertEqual(len(template.product_variant_ids), 4)

    def test_factory_is_not_a_variant_attribute(self):
        """Factory identifies the model; it must not multiply the variants."""
        self.assertFalse(
            self.env["product.attribute"].search([("name", "in", ("Factory", "Model"))])
        )

    def test_every_variant_has_a_unique_sku_and_barcode(self):
        variants = self.env["product.product"].search([("default_code", "!=", False)]).filtered(
            lambda v: v.default_code in OPENING_STOCK
        )
        self.assertEqual(len(variants), len(OPENING_STOCK))
        self.assertEqual(len(set(variants.mapped("default_code"))), len(variants))
        self.assertEqual(len(set(variants.mapped("barcode"))), len(variants))

    def test_barcode_resolves_to_exactly_one_variant(self):
        variant = self._variant()
        resolved = self.env["product.product"].search([("barcode", "=", variant.barcode)])
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved, variant)

    def test_sku_and_barcode_are_different_identifiers(self):
        variant = self._variant()
        self.assertEqual(variant.default_code, "ZALA-2147-BLK-39")
        self.assertNotEqual(variant.default_code, variant.barcode)

    # -- Pricing --------------------------------------------------------
    def test_retail_and_wholesale_pricelists_exist(self):
        for name in ("Retail Pricelist", "Wholesale Pricelist"):
            self.assertTrue(
                self.env["product.pricelist"].search([("name", "=", name)], limit=1), name
            )

    def test_all_variants_of_a_model_share_one_price(self):
        """Mati prices by model, not by size."""
        template = self.env["product.template"].search([("name", "=", "Zala 2147")], limit=1)
        for name, expected in (("Retail Pricelist", 6000.0), ("Wholesale Pricelist", 5200.0)):
            pricelist = self.env["product.pricelist"].search([("name", "=", name)], limit=1)
            prices = {
                variant.default_code: pricelist._get_product_price(variant, 1.0)
                for variant in template.product_variant_ids
            }
            self.assertEqual(
                len({round(price, 2) for price in prices.values()}), 1,
                f"{name} differs across variants: {prices}",
            )
            for price in prices.values():
                self.assertAlmostEqual(price, expected, places=2)

    def test_retail_and_wholesale_differ_without_duplicating_products(self):
        variant = self._variant()
        retail = self.env["product.pricelist"].search([("name", "=", "Retail Pricelist")], limit=1)
        wholesale = self.env["product.pricelist"].search(
            [("name", "=", "Wholesale Pricelist")], limit=1
        )
        self.assertAlmostEqual(retail._get_product_price(variant, 1.0), 6000.0, places=2)
        self.assertAlmostEqual(wholesale._get_product_price(variant, 1.0), 5200.0, places=2)
        # One product record, two prices.
        self.assertEqual(
            len(self.env["product.product"].search([("default_code", "=", variant.default_code)])), 1
        )

    # -- Shops ----------------------------------------------------------
    def test_two_shops_and_no_central_warehouse(self):
        warehouses = self.env["stock.warehouse"].search([])
        codes = set(warehouses.mapped("code"))
        self.assertEqual(codes, {"SHOP1", "SHOP2"})

    def test_opening_stock_is_per_shop(self):
        self._reset_opening_stock()
        variant = self._variant()
        expected = OPENING_STOCK[variant.default_code]
        self.assertEqual(self._qty(variant, "SHOP1"), expected["SHOP1"])
        self.assertEqual(self._qty(variant, "SHOP2"), expected["SHOP2"])
        self.assertNotEqual(expected["SHOP1"], expected["SHOP2"])

    def test_shop_stock_is_independent(self):
        """Moving stock in one shop must not change the other."""
        self._reset_opening_stock()
        variant = self._variant()
        before_shop2 = self._qty(variant, "SHOP2")

        shop1 = self._shop("SHOP1")
        quant = self.env["stock.quant"].with_context(inventory_mode=True).search(
            [("product_id", "=", variant.id), ("location_id", "=", shop1.lot_stock_id.id)], limit=1
        )
        quant.with_context(inventory_mode=True).write({"inventory_quantity": 99})
        quant.with_context(inventory_mode=True).action_apply_inventory()

        self.assertEqual(self._qty(variant, "SHOP1"), 99)
        self.assertEqual(self._qty(variant, "SHOP2"), before_shop2)

    def test_opening_stock_arrived_through_stock_moves(self):
        """No direct quantity writes: every unit is explained by a move."""
        variant = self._variant()
        self.assertTrue(
            self.env["stock.move"].search(
                [("product_id", "=", variant.id), ("state", "=", "done")], limit=1
            )
        )

    # -- Purchasing -----------------------------------------------------
    def test_po_confirmation_does_not_move_stock_and_receipt_does(self):
        """The distinction Mati must see with his own eyes."""
        self._reset_opening_stock()
        variant = self._variant()
        supplier = self.env["res.partner"].search([("name", "=", "ABC Footwear Factory")], limit=1)
        shop1 = self._shop("SHOP1")
        before = self._qty(variant, "SHOP1")
        before_shop2 = self._qty(variant, "SHOP2")

        order = self.env["purchase.order"].create({
            "partner_id": supplier.id,
            "picking_type_id": shop1.in_type_id.id,
            "order_line": [(0, 0, {
                "product_id": variant.id,
                "product_qty": 20,
                "price_unit": 3400.0,
                "name": variant.display_name,
                "product_uom": variant.uom_id.id,
            })],
        })
        order.button_confirm()

        self.assertEqual(order.state, "purchase")
        self.assertEqual(self._qty(variant, "SHOP1"), before, "confirming a PO must not move stock")

        picking = order.picking_ids[:1]
        self.assertTrue(picking)
        self.assertEqual(picking.location_dest_id, shop1.lot_stock_id)
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            if "picked" in move._fields:
                move.picked = True
        picking.button_validate()

        self.assertEqual(picking.state, "done")
        self.assertEqual(self._qty(variant, "SHOP1"), before + 20)
        self.assertEqual(self._qty(variant, "SHOP2"), before_shop2, "Shop 2 must be untouched")

    def test_a_purchase_can_target_shop_2(self):
        self._reset_opening_stock()
        variant = self._variant()
        supplier = self.env["res.partner"].search([("name", "=", "ABC Footwear Factory")], limit=1)
        shop2 = self._shop("SHOP2")
        before_shop1 = self._qty(variant, "SHOP1")
        before_shop2 = self._qty(variant, "SHOP2")

        order = self.env["purchase.order"].create({
            "partner_id": supplier.id,
            "picking_type_id": shop2.in_type_id.id,
            "order_line": [(0, 0, {
                "product_id": variant.id,
                "product_qty": 5,
                "price_unit": 3400.0,
                "name": variant.display_name,
                "product_uom": variant.uom_id.id,
            })],
        })
        order.button_confirm()
        picking = order.picking_ids[:1]
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            if "picked" in move._fields:
                move.picked = True
        picking.button_validate()

        self.assertEqual(self._qty(variant, "SHOP2"), before_shop2 + 5)
        self.assertEqual(self._qty(variant, "SHOP1"), before_shop1)

    # -- Point of sale --------------------------------------------------
    def test_each_shop_has_its_own_till(self):
        shop1 = self.env["pos.config"].search([("name", "=", "Shop 1 POS")], limit=1)
        shop2 = self.env["pos.config"].search([("name", "=", "Shop 2 POS")], limit=1)
        self.assertTrue(shop1 and shop2)
        self.assertEqual(shop1.picking_type_id.warehouse_id.code, "SHOP1")
        self.assertEqual(shop2.picking_type_id.warehouse_id.code, "SHOP2")
        self.assertEqual(shop1.pricelist_id.name, "Retail Pricelist")
        self.assertEqual(shop2.pricelist_id.name, "Wholesale Pricelist")

    def test_both_pricelists_are_reachable_from_either_till(self):
        """A wholesale sale at Shop 1 must not need a duplicate product."""
        for name in ("Shop 1 POS", "Shop 2 POS"):
            config = self.env["pos.config"].search([("name", "=", name)], limit=1)
            available = set(config.available_pricelist_ids.mapped("name"))
            self.assertEqual(available, {"Retail Pricelist", "Wholesale Pricelist"}, name)
            self.assertTrue(config.use_pricelist)

    def test_tills_do_not_share_a_cash_payment_method(self):
        shop1 = self.env["pos.config"].search([("name", "=", "Shop 1 POS")], limit=1)
        shop2 = self.env["pos.config"].search([("name", "=", "Shop 2 POS")], limit=1)
        self.assertFalse(
            set(shop1.payment_method_ids.ids) & set(shop2.payment_method_ids.ids),
            "each shop reconciles its own cash drawer",
        )

    # -- Supplier / taxes ------------------------------------------------
    def test_supplier_and_vendor_price_exist(self):
        supplier = self.env["res.partner"].search([("name", "=", "ABC Footwear Factory")], limit=1)
        self.assertTrue(supplier)
        self.assertGreaterEqual(supplier.supplier_rank, 1)
        template = self.env["product.template"].search([("name", "=", "Zala 2147")], limit=1)
        info = self.env["product.supplierinfo"].search(
            [("partner_id", "=", supplier.id), ("product_tmpl_id", "=", template.id)], limit=1
        )
        self.assertTrue(info)
        self.assertAlmostEqual(info.price, 3400.0, places=2)

    def test_products_carry_a_sale_tax(self):
        template = self.env["product.template"].search([("name", "=", "Zala 2147")], limit=1)
        self.assertTrue(template.taxes_id, "a fiscal document needs a tax block")

    # -- Idempotency -----------------------------------------------------
    def test_seeding_is_idempotent(self):
        """Running the seed twice must not duplicate anything."""
        Product = self.env["product.product"]
        Pricelist = self.env["product.pricelist"]
        before = (
            Product.search_count([("default_code", "in", list(OPENING_STOCK))]),
            Pricelist.search_count([("name", "=", "Retail Pricelist")]),
            self.env["stock.warehouse"].search_count([]),
            self.env["pos.config"].search_count([]),
        )

        self.env["mati.demo.setup"].seed_all()

        after = (
            Product.search_count([("default_code", "in", list(OPENING_STOCK))]),
            Pricelist.search_count([("name", "=", "Retail Pricelist")]),
            self.env["stock.warehouse"].search_count([]),
            self.env["pos.config"].search_count([]),
        )
        self.assertEqual(before, after)


@tagged("post_install", "-at_install", "mati_demo")
class TestIntegrationStatus(TransactionCase):
    """The operator-facing status screen."""

    def test_status_collects_without_raising_when_gateway_is_unreachable(self):
        """A status screen must never be the thing that breaks."""
        self.env["ir.config_parameter"].sudo().set_param(
            "et_fiscal.gateway_base_url", "http://unreachable.invalid:9"
        )
        status = self.env["mati.integration.status"]._collect()
        self.assertFalse(status["gateway_reachable"])
        self.assertIn("fiscal_registered", status)

    def test_status_never_exposes_a_secret(self):
        status = self.env["mati.integration.status"]._collect()
        for key, value in status.items():
            self.assertNotIn("key", key.lower())
            self.assertNotIn("secret", key.lower())
            self.assertNotIn("password", str(value).lower())
