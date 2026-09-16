"""Deterministic demo seeding for Mati's Shoes.

Everything here is CONFIGURATION of vanilla Odoo plus demo records. There is
no retail logic in this module: no custom inventory, no custom pricing, no
custom purchasing. If something in here looks like business logic, it is a bug
in the architecture, not a feature.

Design rules:

* idempotent - running it twice changes nothing;
* deterministic - same SKUs, same barcodes, same quantities every time, so the
  demo script and the verification script can assert exact numbers;
* native mechanisms only - stock arrives through inventory adjustments and
  stock moves, never through direct writes to quantity fields.

Generic Ethiopian fiscal / payment / delivery code does NOT belong here. It
lives in et_fiscal_odoo, external_payment_gateway and external_delivery_gateway.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# The demo dataset. Every number the demo script quotes comes from here.
# ---------------------------------------------------------------------------
COMPANY_NAME = "Mati's Shoes PLC"
COMPANY_TIN = "0012345678"

SUPPLIER_NAME = "ABC Footwear Factory"

COLOR_VALUES = ["Black", "White"]
SIZE_VALUES = ["41", "42"]

# product code -> (name, sku prefix, sales price, cost, barcode product index)
PRODUCTS = {
    "samba": {
        "name": "Adidas Samba",
        "sku_prefix": "SAM",
        "list_price": 6000.0,
        "standard_price": 3200.0,
        "barcode_index": 0,
    },
    "airmax": {
        "name": "Nike Air Max",
        "sku_prefix": "AIR",
        "list_price": 7500.0,
        "standard_price": 4100.0,
        "barcode_index": 1,
    },
}

COLOR_CODES = {"Black": ("BLK", 0), "White": ("WHT", 1)}

WAREHOUSES = [
    ("Mati Main Warehouse", "MAIN"),
    ("Shop 1 Retail", "SHOP1"),
    ("Shop 2 Wholesale", "SHOP2"),
]

# sku -> {warehouse code: opening quantity}
#
# SAM-BLK-42 deliberately opens at 10 in the main warehouse: the purchase
# demo receives 20 more and the expected result is exactly 30.
OPENING_STOCK = {
    "SAM-BLK-41": {"MAIN": 20, "SHOP1": 5, "SHOP2": 8},
    "SAM-BLK-42": {"MAIN": 10, "SHOP1": 3, "SHOP2": 6},
    "SAM-WHT-41": {"MAIN": 18, "SHOP1": 7, "SHOP2": 4},
    "SAM-WHT-42": {"MAIN": 12, "SHOP1": 2, "SHOP2": 9},
    "AIR-BLK-41": {"MAIN": 14, "SHOP1": 4, "SHOP2": 3},
    "AIR-BLK-42": {"MAIN": 11, "SHOP1": 6, "SHOP2": 5},
    "AIR-WHT-41": {"MAIN": 9, "SHOP1": 3, "SHOP2": 2},
    "AIR-WHT-42": {"MAIN": 16, "SHOP1": 5, "SHOP2": 7},
}

# One product identity, four selling contexts. No duplicated products.
PRICELISTS = {
    "retail": {"name": "Retail Pricelist", "samba": 6000.0, "airmax": 7500.0, "min_qty": 1},
    "online": {"name": "Online Pricelist", "samba": 6200.0, "airmax": 7700.0, "min_qty": 1},
    "wholesale": {"name": "Wholesale Pricelist", "samba": 5300.0, "airmax": 6600.0, "min_qty": 1},
}
WHOLESALE_BULK = {"samba": 5000.0, "airmax": 6200.0, "min_qty": 20}

# Variant-specific vendor prices, as quoted in the demo script.
VENDOR_PRICES = {"SAM-BLK-42": 3200.0, "SAM-WHT-42": 3100.0}
VENDOR_DEFAULT_PRICE = {"samba": 3150.0, "airmax": 4100.0}

POS_CONFIGS = [
    ("Shop 1 Retail POS", "SHOP1", "retail"),
    ("Shop 2 Wholesale POS", "SHOP2", "wholesale"),
]


def ean13_check_digit(base12: str) -> str:
    """Standard EAN-13 check digit.

    The demo emits VALID barcodes: an invalid check digit would be rejected by
    a real scanner nomenclature and makes the barcode demo a lie.
    """
    total = sum(int(digit) * (3 if index % 2 else 1) for index, digit in enumerate(base12))
    return str((10 - total % 10) % 10)


def build_barcode(product_index: int, color_index: int, size: str) -> str:
    """Deterministic, valid EAN-13 for one variant.

    Layout: 200 | product(2) | color(1) | size(2) | filler(4) | check(1)
    The 200 prefix is the range reserved for in-store use.
    """
    base12 = f"200{product_index:02d}{color_index:01d}{int(size):02d}0000"
    return base12 + ean13_check_digit(base12)


class MatiDemoSetup(models.TransientModel):
    """Demo control panel.

    Transient because it holds no state: it is a set of actions, and the
    seeded data lives in ordinary Odoo records.
    """

    _name = "mati.demo.setup"
    _description = "Mati Demo Environment"

    name = fields.Char(default="Mati Demo", readonly=True)

    # ==================================================================
    # Entry point
    # ==================================================================
    @api.model
    def seed_all(self):
        """Seed everything. Idempotent: safe to run repeatedly."""
        _logger.info("=== Mati demo seeding: start ===")
        self._seed_feature_groups()
        company = self._seed_company()
        self._seed_accounting(company)
        self._seed_partners(company)
        attributes = self._seed_attributes()
        templates = self._seed_products(company, attributes)
        self._seed_product_taxes(company, templates)
        self._seed_variant_identity(templates)
        self._seed_vendor_prices(templates)
        pricelists = self._seed_pricelists(company, templates)
        warehouses = self._seed_warehouses(company)
        self._seed_opening_stock(company, warehouses)
        self._seed_pos_configs(company, warehouses, pricelists)
        self._seed_website(pricelists, templates)
        _logger.info("=== Mati demo seeding: done ===")
        return True

    # ==================================================================
    # Odoo feature switches
    # ==================================================================
    @api.model
    def _seed_feature_groups(self):
        """Turn on the Odoo features this demo depends on.

        These are the same switches as the checkboxes in Settings. Without
        them Odoo hides variants and pricelists in the UI and refuses a second
        warehouse - which would make the demo impossible to show even though
        the data underneath is correct.
        """
        wanted = [
            "product.group_product_variant",       # Variants
            "product.group_product_pricelist",     # Pricelists
            "stock.group_stock_multi_locations",   # Storage locations
            "stock.group_stock_multi_warehouses",  # Multiple warehouses
        ]
        base_user = self.env.ref("base.group_user", raise_if_not_found=False)
        if not base_user:  # pragma: no cover - defensive
            return False

        enabled = []
        for xmlid in wanted:
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if not group:
                _logger.warning("feature group %s not found; skipping", xmlid)
                continue
            if group not in base_user.implied_ids:
                base_user.write({"implied_ids": [(4, group.id)]})
                enabled.append(xmlid)
        _logger.info("feature groups enabled: %s", enabled or "already on")
        return True

    # ==================================================================
    # Company and partners
    # ==================================================================
    @api.model
    def _seed_company(self):
        company = self.env.company
        etb = self.env.ref("base.ETB", raise_if_not_found=False)
        values = {
            "name": COMPANY_NAME,
            "et_fiscal_tin": COMPANY_TIN,
            "street": "Bole Road",
            "city": "Addis Ababa",
            "phone": "+251 11 000 0000",
            "email": "info@matishoes.example",
        }
        ethiopia = self.env.ref("base.et", raise_if_not_found=False)
        if ethiopia:
            values["country_id"] = ethiopia.id
        if etb:
            if not etb.active:
                etb.active = True
            # Changing the currency is only possible before any journal entry
            # exists. On a freshly bootstrapped database it always is.
            if company.currency_id != etb:
                try:
                    company.write({"currency_id": etb.id})
                except Exception:  # noqa: BLE001
                    _logger.warning(
                        "could not switch the company currency to ETB; "
                        "continuing with %s",
                        company.currency_id.name,
                    )
        company.write(values)
        _logger.info("company: %s (TIN %s, %s)", company.name, COMPANY_TIN, company.currency_id.name)
        return company

    @api.model
    def _seed_accounting(self, company):
        """Load a chart of accounts and make sure a 15% VAT exists.

        Without a chart of accounts there are no journals, and without journals
        the point of sale cannot open a session at all. Odoo has no Ethiopian
        localisation package, so the generic chart is used - and its default
        15% rate happens to match Ethiopian VAT.
        """
        if not company.chart_template:
            template = self.env["account.chart.template"]
            try:
                template.try_loading("generic_coa", company=company, install_demo=False)
                _logger.info("accounting: loaded the generic chart of accounts")
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "could not load a chart of accounts; POS and invoicing will not work"
                )
                return False

        tax = self._get_sale_tax(company)
        if not tax:
            tax = self.env["account.tax"].create(
                {
                    "name": "VAT 15%",
                    "amount": 15.0,
                    "amount_type": "percent",
                    "type_tax_use": "sale",
                    "company_id": company.id,
                    "description": "VAT15",
                }
            )
            _logger.info("accounting: created %s", tax.name)
        elif not tax.description:
            # The fiscal contract sends `description` as the tax code.
            tax.description = "VAT15"
        return True

    @api.model
    def _get_sale_tax(self, company):
        return self.env["account.tax"].search(
            [
                ("type_tax_use", "=", "sale"),
                ("company_id", "=", company.id),
                ("amount_type", "=", "percent"),
            ],
            order="amount desc",
            limit=1,
        )

    @api.model
    def _seed_product_taxes(self, company, templates):
        """Put the sale tax on the demo products.

        A fiscal document with no tax block is not a useful demonstration of
        fiscalization.
        """
        tax = self._get_sale_tax(company)
        if not tax:
            _logger.warning("no sale tax available; demo products will be untaxed")
            return False
        for template in templates.values():
            if tax not in template.taxes_id:
                template.taxes_id = [(6, 0, tax.ids)]
        _logger.info("products: sale tax %s (%.2f%%) applied", tax.name, tax.amount)
        return True

    @api.model
    def _seed_partners(self, company):
        Partner = self.env["res.partner"]
        supplier = Partner.search([("name", "=", SUPPLIER_NAME)], limit=1)
        values = {
            "name": SUPPLIER_NAME,
            "company_type": "company",
            "supplier_rank": 1,
            "street": "Industrial Zone",
            "city": "Addis Ababa",
            "phone": "+251 11 111 1111",
            "email": "sales@abcfootwear.example",
        }
        ethiopia = self.env.ref("base.et", raise_if_not_found=False)
        if ethiopia:
            values["country_id"] = ethiopia.id
        if supplier:
            supplier.write(values)
        else:
            supplier = Partner.create(values)

        wholesale_customer = Partner.search([("name", "=", "Bole Shoe Traders")], limit=1)
        if not wholesale_customer:
            Partner.create(
                {
                    "name": "Bole Shoe Traders",
                    "company_type": "company",
                    "customer_rank": 1,
                    "city": "Addis Ababa",
                    "et_fiscal_tin": "0098765432",
                }
            )
        _logger.info("supplier: %s", supplier.display_name)
        return supplier

    # ==================================================================
    # Product model: native templates, attributes and variants
    # ==================================================================
    @api.model
    def _seed_attributes(self):
        Attribute = self.env["product.attribute"]
        Value = self.env["product.attribute.value"]
        result = {}
        for name, values in (("Color", COLOR_VALUES), ("Size", SIZE_VALUES)):
            attribute = Attribute.search([("name", "=", name)], limit=1)
            if not attribute:
                attribute = Attribute.create(
                    {"name": name, "create_variant": "always", "display_type": "radio"}
                )
            value_records = self.env["product.attribute.value"]
            for sequence, value_name in enumerate(values):
                value = Value.search(
                    [("name", "=", value_name), ("attribute_id", "=", attribute.id)], limit=1
                )
                if not value:
                    value = Value.create(
                        {
                            "name": value_name,
                            "attribute_id": attribute.id,
                            "sequence": sequence,
                        }
                    )
                value_records |= value
            result[name] = (attribute, value_records)
        _logger.info("attributes: Color(%s) Size(%s)", ", ".join(COLOR_VALUES), ", ".join(SIZE_VALUES))
        return result

    @api.model
    def _seed_products(self, company, attributes):
        Template = self.env["product.template"]
        color_attribute, color_values = attributes["Color"]
        size_attribute, size_values = attributes["Size"]

        templates = {}
        for code, spec in PRODUCTS.items():
            template = Template.search([("name", "=", spec["name"])], limit=1)
            values = {
                "name": spec["name"],
                "type": "consu",
                "is_storable": True,
                "list_price": spec["list_price"],
                "standard_price": spec["standard_price"],
                "sale_ok": True,
                "purchase_ok": True,
                "available_in_pos": True,
                "categ_id": self.env.ref("product.product_category_all").id,
                "invoice_policy": "order",
            }
            if template:
                template.write(values)
            else:
                template = Template.create(values)

            # One attribute line per attribute; Odoo generates the variants.
            self._ensure_attribute_line(template, color_attribute, color_values)
            self._ensure_attribute_line(template, size_attribute, size_values)
            templates[code] = template
            _logger.info(
                "product: %s -> %s variants", template.name, len(template.product_variant_ids)
            )
        return templates

    @api.model
    def _ensure_attribute_line(self, template, attribute, values):
        line = template.attribute_line_ids.filtered(lambda l: l.attribute_id == attribute)
        if line:
            missing = values - line.value_ids
            if missing:
                line.value_ids = [(4, value.id) for value in missing]
            return line
        return self.env["product.template.attribute.line"].create(
            {
                "product_tmpl_id": template.id,
                "attribute_id": attribute.id,
                "value_ids": [(6, 0, values.ids)],
            }
        )

    @api.model
    def _seed_variant_identity(self, templates):
        """Give every concrete variant its SKU and barcode.

        SKU and barcode are different things: the SKU is how the business names
        the variant, the barcode is what a scanner reads. Both resolve to the
        same product.product.
        """
        assigned = 0
        for code, template in templates.items():
            spec = PRODUCTS[code]
            for variant in template.product_variant_ids:
                color = self._variant_attribute_value(variant, "Color")
                size = self._variant_attribute_value(variant, "Size")
                if not color or not size:
                    continue
                color_code, color_index = COLOR_CODES[color]
                sku = f"{spec['sku_prefix']}-{color_code}-{size}"
                barcode = build_barcode(spec["barcode_index"], color_index, size)
                if variant.default_code != sku or variant.barcode != barcode:
                    variant.write({"default_code": sku, "barcode": barcode})
                assigned += 1
                _logger.info("variant: %s -> SKU %s barcode %s", variant.display_name, sku, barcode)
        _logger.info("variant identity assigned for %s variants", assigned)
        return assigned

    @api.model
    def _variant_attribute_value(self, variant, attribute_name):
        for value in variant.product_template_attribute_value_ids:
            if value.attribute_id.name == attribute_name:
                return value.name
        return None

    @api.model
    def _variant_by_sku(self, sku):
        variant = self.env["product.product"].search([("default_code", "=", sku)], limit=1)
        if not variant:
            raise UserError(_("Demo data is missing: no product variant with SKU %s.") % sku)
        return variant

    # ==================================================================
    # Purchasing: vendor prices
    # ==================================================================
    @api.model
    def _seed_vendor_prices(self, templates):
        SupplierInfo = self.env["product.supplierinfo"]
        supplier = self.env["res.partner"].search([("name", "=", SUPPLIER_NAME)], limit=1)
        if not supplier:
            return

        for code, template in templates.items():
            existing = SupplierInfo.search(
                [
                    ("partner_id", "=", supplier.id),
                    ("product_tmpl_id", "=", template.id),
                    ("product_id", "=", False),
                ],
                limit=1,
            )
            values = {
                "partner_id": supplier.id,
                "product_tmpl_id": template.id,
                "price": VENDOR_DEFAULT_PRICE[code],
                "min_qty": 1,
                "delay": 7,
            }
            if existing:
                existing.write(values)
            else:
                SupplierInfo.create(values)

        for sku, price in VENDOR_PRICES.items():
            variant = self.env["product.product"].search([("default_code", "=", sku)], limit=1)
            if not variant:
                continue
            existing = SupplierInfo.search(
                [("partner_id", "=", supplier.id), ("product_id", "=", variant.id)], limit=1
            )
            values = {
                "partner_id": supplier.id,
                "product_tmpl_id": variant.product_tmpl_id.id,
                "product_id": variant.id,
                "price": price,
                "min_qty": 1,
                "delay": 7,
            }
            if existing:
                existing.write(values)
            else:
                SupplierInfo.create(values)
            _logger.info("vendor price: %s = %.2f from %s", sku, price, supplier.name)

    # ==================================================================
    # Pricing: native pricelists, one product identity
    # ==================================================================
    @api.model
    def _seed_pricelists(self, company, templates):
        Pricelist = self.env["product.pricelist"]
        Item = self.env["product.pricelist.item"]
        currency = company.currency_id
        result = {}

        for key, spec in PRICELISTS.items():
            pricelist = Pricelist.search([("name", "=", spec["name"])], limit=1)
            values = {
                "name": spec["name"],
                "currency_id": currency.id,
                "company_id": company.id,
                "sequence": {"retail": 10, "online": 20, "wholesale": 30}[key],
            }
            # `selectable` is contributed by website_sale; only set it if present.
            if "selectable" in Pricelist._fields:
                values["selectable"] = True
            if pricelist:
                pricelist.write(values)
            else:
                pricelist = Pricelist.create(values)

            for product_code, template in templates.items():
                self._ensure_pricelist_item(
                    Item, pricelist, template, spec[product_code], spec["min_qty"]
                )
            result[key] = pricelist
            _logger.info("pricelist: %s", pricelist.name)

        # Quantity break: the same variant is cheaper by the case.
        wholesale = result["wholesale"]
        for product_code, template in templates.items():
            self._ensure_pricelist_item(
                Item,
                wholesale,
                template,
                WHOLESALE_BULK[product_code],
                WHOLESALE_BULK["min_qty"],
            )
            _logger.info(
                "pricelist: %s bulk rule %s+ units = %.2f",
                wholesale.name,
                WHOLESALE_BULK["min_qty"],
                WHOLESALE_BULK[product_code],
            )
        return result

    @api.model
    def _ensure_pricelist_item(self, Item, pricelist, template, price, min_qty):
        existing = Item.search(
            [
                ("pricelist_id", "=", pricelist.id),
                ("product_tmpl_id", "=", template.id),
                ("min_quantity", "=", min_qty),
                ("applied_on", "=", "1_product"),
            ],
            limit=1,
        )
        values = {
            "pricelist_id": pricelist.id,
            "applied_on": "1_product",
            "product_tmpl_id": template.id,
            "compute_price": "fixed",
            "fixed_price": price,
            "min_quantity": min_qty,
        }
        if existing:
            existing.write(values)
            return existing
        return Item.create(values)

    # ==================================================================
    # Locations: native warehouses
    # ==================================================================
    @api.model
    def _seed_warehouses(self, company):
        Warehouse = self.env["stock.warehouse"]
        result = {}
        existing = Warehouse.search([("company_id", "=", company.id)], order="id")

        for index, (name, code) in enumerate(WAREHOUSES):
            warehouse = Warehouse.search(
                [("code", "=", code), ("company_id", "=", company.id)], limit=1
            )
            if not warehouse and index == 0 and existing:
                # Reuse the warehouse Odoo created with the company rather than
                # leaving an orphan "YourCompany" warehouse behind.
                warehouse = existing[0]
                warehouse.write({"name": name, "code": code})
            elif not warehouse:
                warehouse = Warehouse.create(
                    {"name": name, "code": code, "company_id": company.id}
                )
            else:
                warehouse.write({"name": name})
            result[code] = warehouse
            _logger.info("warehouse: %s (%s) stock location %s", name, code, warehouse.lot_stock_id.complete_name)
        return result

    # ==================================================================
    # Opening stock: native inventory adjustments, never direct writes
    # ==================================================================
    @api.model
    def _seed_opening_stock(self, company, warehouses):
        Quant = self.env["stock.quant"]
        applied = 0
        for sku, quantities in OPENING_STOCK.items():
            variant = self.env["product.product"].search([("default_code", "=", sku)], limit=1)
            if not variant:
                _logger.warning("opening stock: no variant for SKU %s", sku)
                continue
            for warehouse_code, quantity in quantities.items():
                warehouse = warehouses.get(warehouse_code)
                if not warehouse:
                    continue
                location = warehouse.lot_stock_id
                current = Quant._get_available_quantity(variant, location)
                if abs(current - quantity) < 0.0001:
                    continue

                # Inventory adjustment: Odoo creates the stock move from the
                # inventory-loss location. This is the native mechanism and it
                # leaves a proper audit trail.
                quant = Quant.with_context(inventory_mode=True).search(
                    [("product_id", "=", variant.id), ("location_id", "=", location.id)], limit=1
                )
                if quant:
                    quant.with_context(inventory_mode=True).write(
                        {"inventory_quantity": quantity}
                    )
                else:
                    quant = Quant.with_context(inventory_mode=True).create(
                        {
                            "product_id": variant.id,
                            "location_id": location.id,
                            "inventory_quantity": quantity,
                        }
                    )
                quant.with_context(inventory_mode=True).action_apply_inventory()
                applied += 1
                _logger.info("opening stock: %s @ %s = %s", sku, warehouse_code, quantity)
        _logger.info("opening stock applied for %s product/location pairs", applied)
        return applied

    # ==================================================================
    # Point of sale
    # ==================================================================
    @api.model
    def _seed_pos_configs(self, company, warehouses, pricelists):
        Config = self.env["pos.config"]
        payment_methods = self._ensure_pos_payment_methods(company)

        for name, warehouse_code, pricelist_key in POS_CONFIGS:
            warehouse = warehouses.get(warehouse_code)
            pricelist = pricelists.get(pricelist_key)
            if not warehouse or not pricelist:
                continue

            config = Config.search([("name", "=", name), ("company_id", "=", company.id)], limit=1)
            picking_type = warehouse.out_type_id
            values = {
                "name": name,
                "company_id": company.id,
                # This is what makes the sale decrement THIS shop's stock.
                "picking_type_id": picking_type.id,
                "use_pricelist": True,
                "pricelist_id": pricelist.id,
                "available_pricelist_ids": [(6, 0, [pricelist.id])],
            }
            if payment_methods:
                values["payment_method_ids"] = [(6, 0, payment_methods.ids)]

            if config:
                config.write(values)
            else:
                config = Config.create(values)
            _logger.info(
                "pos: %s -> picking type %s, pricelist %s",
                name,
                picking_type.display_name,
                pricelist.name,
            )
        return True

    @api.model
    def _ensure_pos_payment_methods(self, company):
        Method = self.env["pos.payment.method"]
        methods = Method.search([("company_id", "=", company.id)])
        if methods:
            return methods

        journal = self.env["account.journal"].search(
            [("type", "=", "cash"), ("company_id", "=", company.id)], limit=1
        )
        if not journal:
            _logger.warning("no cash journal found; POS payment method not created")
            return Method
        return Method.create(
            {
                "name": "Cash",
                "company_id": company.id,
                "journal_id": journal.id,
            }
        )

    # ==================================================================
    # Website / e-commerce (only if website_sale is installed)
    # ==================================================================
    @api.model
    def _seed_website(self, pricelists, templates):
        if "website" not in self.env:
            _logger.info("website not installed; skipping e-commerce configuration")
            return False

        online = pricelists.get("online")
        website = self.env["website"].search([], limit=1)
        if website and online:
            # Same catalog, same variants, same stock - only the price context
            # differs. No separate e-commerce product database.
            if "pricelist_id" in self.env["website"]._fields:
                try:
                    website.write({"company_id": self.env.company.id})
                except Exception:  # noqa: BLE001
                    _logger.warning("could not set the website company")

        published = 0
        for template in templates.values():
            if "is_published" in template._fields and not template.is_published:
                template.is_published = True
                published += 1
        _logger.info("website: published %s product templates", published)
        return True

    # ==================================================================
    # Demo helper actions (the buttons used while sitting with the client)
    # ==================================================================
    @api.model
    def action_seed(self):
        self.seed_all()
        return self._notify(_("Demo data seeded."))

    @api.model
    def action_reset_demo(self):
        """Return to the seeded opening state.

        Cancels demo purchase orders and transfers created during the demo and
        re-applies the opening inventory. Fiscal transactions are deliberately
        NOT deleted: destroying a registration history is exactly what a fiscal
        system must never do.
        """
        company = self.env.company
        warehouses = {
            warehouse.code: warehouse
            for warehouse in self.env["stock.warehouse"].search([("company_id", "=", company.id)])
        }

        orders = self.env["purchase.order"].search(
            [("state", "in", ("draft", "sent", "to approve", "purchase"))]
        )
        for order in orders:
            try:
                order.button_cancel()
            except Exception:  # noqa: BLE001
                _logger.warning("could not cancel purchase order %s", order.name)

        pickings = self.env["stock.picking"].search([("state", "not in", ("done", "cancel"))])
        for picking in pickings:
            try:
                picking.action_cancel()
            except Exception:  # noqa: BLE001
                _logger.warning("could not cancel picking %s", picking.name)

        self._seed_opening_stock(company, warehouses)
        return self._notify(_("Demo reset to the seeded opening state."))

    @api.model
    def action_toggle_fiscal_failure(self):
        """Flip the mock fiscal provider between working and failing."""
        client = self.env["et.fiscal.gateway.client"]
        config = client._get_config()
        import json

        import requests

        url = f"{config['base_url']}/api/v1/admin/mock/fiscal/failure-mode"
        headers = {"X-API-Key": config["api_key"], "Content-Type": "application/json"}
        current = requests.get(url, headers=headers, timeout=config["timeout"]).json()
        target = not current.get("failure_mode", False)
        requests.post(
            url, data=json.dumps({"enabled": target}), headers=headers, timeout=config["timeout"]
        )
        return self._notify(
            _("Mock fiscal provider failure mode is now %s.") % ("ON" if target else "OFF")
        )

    @api.model
    def _notify(self, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": "success", "title": _("Mati Demo"), "message": message, "sticky": False},
        }

    # ==================================================================
    # Introspection used by the demo script and verification
    # ==================================================================
    @api.model
    def demo_snapshot(self):
        """Current stock and prices for the demo SKUs, as plain data."""
        company = self.env.company
        warehouses = {
            warehouse.code: warehouse
            for warehouse in self.env["stock.warehouse"].search([("company_id", "=", company.id)])
        }
        Quant = self.env["stock.quant"]

        stock = {}
        for sku in OPENING_STOCK:
            variant = self.env["product.product"].search([("default_code", "=", sku)], limit=1)
            if not variant:
                continue
            stock[sku] = {
                code: Quant._get_available_quantity(variant, warehouse.lot_stock_id)
                for code, warehouse in warehouses.items()
            }

        prices = {}
        samba = self.env["product.product"].search([("default_code", "=", "SAM-BLK-42")], limit=1)
        if samba:
            for key, spec in PRICELISTS.items():
                pricelist = self.env["product.pricelist"].search(
                    [("name", "=", spec["name"])], limit=1
                )
                if pricelist:
                    prices[key] = pricelist._get_product_price(samba, 1.0)
            wholesale = self.env["product.pricelist"].search(
                [("name", "=", PRICELISTS["wholesale"]["name"])], limit=1
            )
            if wholesale:
                prices["wholesale_bulk"] = wholesale._get_product_price(
                    samba, float(WHOLESALE_BULK["min_qty"])
                )

        return {"stock": stock, "prices": prices}
