"""Deterministic demo seeding for Mati's Shoes.

Everything here is CONFIGURATION of vanilla Odoo plus demo records. There is
no retail logic in this module: no custom inventory, no custom pricing, no
custom purchasing. If something in here looks like business logic, it is a bug
in the architecture, not a feature.

Design rules:

* idempotent - running it twice writes nothing at all;
* deterministic - same SKUs, barcodes and quantities every time, so the demo
  script and the verification script can assert exact numbers;
* native mechanisms only - stock arrives through inventory adjustments and
  stock moves, never through direct writes to quantity fields.

The dataset itself lives in ``mati_demo_data.py``. Generic Ethiopian fiscal,
payment or delivery code does NOT belong here - that is reusable
infrastructure in et_fiscal_odoo / external_payment_gateway /
external_delivery_gateway.
"""

import json
import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .mati_demo_data import (
    ALL_COLORS,
    ALL_SIZES,
    COLOR_CODES,
    COMPANY_NAME,
    COMPANY_TIN,
    DEMO_PURCHASE,
    OPENING_STOCK,
    POS_CONFIGS,
    PRICELISTS,
    PRODUCTS,
    SHOPS,
    SUPPLIER_NAME,
    WHOLESALE_CUSTOMER,
    build_barcode,
    build_sku,
)

_logger = logging.getLogger(__name__)


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
        # Must come AFTER the chart of accounts: see _seed_currency.
        self._seed_currency(company)
        self._seed_partners(company)
        attributes = self._seed_attributes()
        templates = self._seed_products(company, attributes)
        self._seed_product_taxes(company, templates)
        self._seed_variant_identity(templates)
        self._seed_vendor_prices(templates)
        pricelists = self._seed_pricelists(company, templates)
        shops = self._seed_shops(company)
        self._seed_opening_stock(company, shops)
        self._seed_pos_configs(company, shops, pricelists)
        self._seed_website(templates)
        _logger.info("=== Mati demo seeding: done ===")
        return True

    # ==================================================================
    # Odoo feature switches
    # ==================================================================
    @api.model
    def _seed_feature_groups(self):
        """Turn on the Odoo features this demo depends on.

        The same switches as the checkboxes in Settings. Without them Odoo
        hides variants and pricelists in the UI and refuses a second
        warehouse - which would make the demo impossible to show even though
        the data underneath is correct.
        """
        wanted = [
            "product.group_product_variant",       # Variants
            "product.group_product_pricelist",     # Pricelists
            "stock.group_stock_multi_locations",   # Storage locations
            "stock.group_stock_multi_warehouses",  # Multiple warehouses (shops)
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
    # Company - ONE legal entity, ONE TIN, two operational shops
    # ==================================================================
    @api.model
    def _seed_company(self):
        company = self.env.company
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
        etb = self.env.ref("base.ETB", raise_if_not_found=False)
        if etb and not etb.active:
            etb.active = True
        company.write(self._changed_values(company, values))
        # NB: the currency is deliberately NOT set here - see _seed_currency.
        _logger.info("company: %s (TIN %s)", company.name, COMPANY_TIN)
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
    def _seed_currency(self, company):
        """Set ETB, AFTER the chart of accounts has been loaded.

        ``account.chart.template.try_loading()`` writes the company currency
        from ``account_fiscal_country_id``, which for the generic chart is the
        United States. Setting ETB before loading the chart therefore looks
        like it works and is then silently overwritten with USD - and every
        price in the demo comes out in dollars.
        """
        etb = self.env.ref("base.ETB", raise_if_not_found=False)
        ethiopia = self.env.ref("base.et", raise_if_not_found=False)
        if not etb:
            _logger.warning("ETB currency not found; leaving %s", company.currency_id.name)
            return False
        if not etb.active:
            etb.active = True

        values = {}
        if ethiopia and company.account_fiscal_country_id != ethiopia:
            values["account_fiscal_country_id"] = ethiopia.id
        if company.currency_id != etb:
            values["currency_id"] = etb.id
        if values:
            try:
                company.write(values)
            except Exception:  # noqa: BLE001
                _logger.exception("could not switch the company to ETB")
                return False

        _logger.info(
            "company currency: %s (fiscal country %s)",
            company.currency_id.name,
            company.account_fiscal_country_id.code or "-",
        )
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
        """A fiscal document with no tax block is a poor demonstration."""
        tax = self._get_sale_tax(company)
        if not tax:
            _logger.warning("no sale tax available; demo products will be untaxed")
            return False
        for template in templates.values():
            if tax not in template.taxes_id:
                template.taxes_id = [(6, 0, tax.ids)]
        _logger.info("products: sale tax %s (%.2f%%) applied", tax.name, tax.amount)
        return True

    # ==================================================================
    # Partners
    # ==================================================================
    @api.model
    def _seed_partners(self, company):
        Partner = self.env["res.partner"]
        ethiopia = self.env.ref("base.et", raise_if_not_found=False)

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
        if ethiopia:
            values["country_id"] = ethiopia.id
        if supplier:
            supplier.write(self._changed_values(supplier, values))
        else:
            supplier = Partner.create(values)

        if not Partner.search([("name", "=", WHOLESALE_CUSTOMER)], limit=1):
            Partner.create(
                {
                    "name": WHOLESALE_CUSTOMER,
                    "company_type": "company",
                    "customer_rank": 1,
                    "city": "Addis Ababa",
                    "et_fiscal_tin": "0098765432",
                }
            )
        _logger.info("supplier: %s", supplier.display_name)
        return supplier

    # ==================================================================
    # Catalogue: Factory + Model -> template, Colour + Size -> variants
    # ==================================================================
    @api.model
    def _seed_attributes(self):
        Attribute = self.env["product.attribute"]
        Value = self.env["product.attribute.value"]
        result = {}
        for name, values in (("Color", ALL_COLORS), ("Size", ALL_SIZES)):
            attribute = Attribute.search([("name", "=", name)], limit=1)
            if not attribute:
                attribute = Attribute.create(
                    {"name": name, "create_variant": "always", "display_type": "radio"}
                )
            by_name = {}
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
                by_name[value_name] = value
            result[name] = (attribute, by_name)
        _logger.info("attributes: Color(%s) Size(%s)", ", ".join(ALL_COLORS), ", ".join(ALL_SIZES))
        return result

    @api.model
    def _seed_products(self, company, attributes):
        """One template per Factory + Model. Colour and Size make the variants."""
        Template = self.env["product.template"]
        color_attribute, color_values = attributes["Color"]
        size_attribute, size_values = attributes["Size"]
        categ = self.env.ref("product.product_category_all")

        templates = {}
        for key, spec in PRODUCTS.items():
            name = f"{spec['factory']} {spec['model']}"
            template = Template.search([("name", "=", name)], limit=1)
            values = {
                "name": name,
                "shoe_factory": spec["factory"],
                "shoe_model": spec["model"],
                "type": "consu",
                "is_storable": True,
                "list_price": spec["retail_price"],
                "standard_price": spec["cost"],
                "sale_ok": True,
                "purchase_ok": True,
                "available_in_pos": True,
                "categ_id": categ.id,
                "invoice_policy": "order",
            }
            if template:
                template.write(self._changed_values(template, values))
            else:
                template = Template.create(values)

            # Only this model's colours and sizes; Odoo generates the variants.
            self._ensure_attribute_line(
                template, color_attribute, [color_values[c] for c in spec["colors"]]
            )
            self._ensure_attribute_line(
                template, size_attribute, [size_values[s] for s in spec["sizes"]]
            )
            templates[key] = template
            _logger.info(
                "product: %s (factory %s, model %s) -> %s variants",
                template.name,
                spec["factory"],
                spec["model"],
                len(template.product_variant_ids),
            )
        return templates

    @api.model
    def _ensure_attribute_line(self, template, attribute, values):
        value_ids = [value.id for value in values]
        line = template.attribute_line_ids.filtered(lambda l: l.attribute_id == attribute)
        if line:
            if set(line.value_ids.ids) != set(value_ids):
                line.value_ids = [(6, 0, value_ids)]
            return line
        return self.env["product.template.attribute.line"].create(
            {
                "product_tmpl_id": template.id,
                "attribute_id": attribute.id,
                "value_ids": [(6, 0, value_ids)],
            }
        )

    @api.model
    def _seed_variant_identity(self, templates):
        """Give every concrete variant its SKU and barcode.

        SKU and barcode are different things: the SKU is how the business
        names the variant, the barcode is what a scanner reads. Mati generates
        barcodes in external label software - Odoo's job is only to store the
        same value against the right variant.
        """
        assigned = 0
        for key, template in templates.items():
            spec = PRODUCTS[key]
            for variant in template.product_variant_ids:
                color = self._variant_attribute_value(variant, "Color")
                size = self._variant_attribute_value(variant, "Size")
                if not color or not size:
                    continue
                sku = build_sku(spec, color, size)
                barcode = build_barcode(spec["barcode_index"], COLOR_CODES[color][1], size)
                values = self._changed_values(variant, {"default_code": sku, "barcode": barcode})
                if values:
                    variant.write(values)
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
            return False

        for key, template in templates.items():
            spec = PRODUCTS[key]
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
                "price": spec["cost"],
                "min_qty": 1,
                "delay": 7,
            }
            if existing:
                existing.write(self._changed_values(existing, values))
            else:
                SupplierInfo.create(values)
            _logger.info("vendor price: %s = %.2f from %s", template.name, spec["cost"], supplier.name)
        return True

    # ==================================================================
    # Pricing: two contexts, one product identity
    # ==================================================================
    @api.model
    def _seed_pricelists(self, company, templates):
        """Retail and wholesale, priced per model - not per variant.

        All colours and sizes of one Factory + Model share the same price,
        which is how Mati actually sells. One rule per template therefore
        covers every variant of it.
        """
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
                "sequence": spec["sequence"],
            }
            # `selectable` is contributed by website_sale; only set it if present.
            if "selectable" in Pricelist._fields:
                values["selectable"] = True

            if pricelist:
                pricelist.write(self._changed_values(pricelist, values))
            else:
                pricelist = Pricelist.create(values)

            for product_key, template in templates.items():
                price = PRODUCTS[product_key][spec["field"]]
                self._ensure_pricelist_item(Item, pricelist, template, price)
                _logger.info("pricelist %s: %s = %.2f", spec["name"], template.name, price)
            result[key] = pricelist
        return result

    @api.model
    def _ensure_pricelist_item(self, Item, pricelist, template, price):
        existing = Item.search(
            [
                ("pricelist_id", "=", pricelist.id),
                ("product_tmpl_id", "=", template.id),
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
            "min_quantity": 1,
        }
        if existing:
            changed = self._changed_values(existing, values)
            if changed:
                existing.write(changed)
            return existing
        return Item.create(values)

    # ==================================================================
    # Shops. No central warehouse: the supplier delivers to the shop.
    # ==================================================================
    @api.model
    def _seed_shops(self, company):
        """Two shops, each its own stock location and its own receipts.

        Odoo creates one warehouse with the company; that one becomes Shop 1
        rather than being left behind as an unused "Main Warehouse". Mati has
        no central warehouse and the demo must not imply one.
        """
        Warehouse = self.env["stock.warehouse"]
        result = {}
        existing = Warehouse.search([("company_id", "=", company.id)], order="id")

        for index, shop in enumerate(SHOPS):
            warehouse = Warehouse.search(
                [("code", "=", shop["code"]), ("company_id", "=", company.id)], limit=1
            )
            if not warehouse and index == 0 and existing:
                warehouse = existing[0]
                warehouse.write({"name": shop["name"], "code": shop["code"]})
            elif not warehouse:
                warehouse = Warehouse.create(
                    {"name": shop["name"], "code": shop["code"], "company_id": company.id}
                )
            else:
                changed = self._changed_values(warehouse, {"name": shop["name"]})
                if changed:
                    warehouse.write(changed)
            result[shop["code"]] = warehouse
            _logger.info(
                "shop: %s (%s) stock %s, receipts %s",
                shop["name"],
                shop["code"],
                warehouse.lot_stock_id.complete_name,
                warehouse.in_type_id.display_name,
            )
        return result

    # ==================================================================
    # Opening stock: native inventory adjustments, never direct writes
    # ==================================================================
    @api.model
    def _seed_opening_stock(self, company, shops):
        """Quantity is stock at a shop, not product master data."""
        Quant = self.env["stock.quant"]
        applied = 0
        for sku, quantities in OPENING_STOCK.items():
            variant = self.env["product.product"].search([("default_code", "=", sku)], limit=1)
            if not variant:
                _logger.warning("opening stock: no variant for SKU %s", sku)
                continue
            for shop_code, quantity in quantities.items():
                warehouse = shops.get(shop_code)
                if not warehouse:
                    continue
                location = warehouse.lot_stock_id
                current = Quant._get_available_quantity(variant, location)
                if abs(current - quantity) < 0.0001:
                    continue

                # Inventory adjustment: Odoo creates the stock move from the
                # inventory-loss location. Native mechanism, proper audit trail.
                quant = Quant.with_context(inventory_mode=True).search(
                    [("product_id", "=", variant.id), ("location_id", "=", location.id)], limit=1
                )
                if quant:
                    quant.with_context(inventory_mode=True).write({"inventory_quantity": quantity})
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
                _logger.info("opening stock: %s @ %s = %s", sku, shop_code, quantity)
        _logger.info("opening stock applied for %s product/shop pairs", applied)
        return applied

    # ==================================================================
    # Point of sale - one till per shop
    # ==================================================================
    @api.model
    def _seed_pos_configs(self, company, shops, pricelists):
        Config = self.env["pos.config"]
        # Both tills can reach both pricelists, so a wholesale sale can be rung
        # up at Shop 1 without duplicating a single product.
        available = [pricelists[key].id for key in ("retail", "wholesale") if key in pricelists]

        for spec in POS_CONFIGS:
            warehouse = shops.get(spec["shop"])
            default_pricelist = pricelists.get(spec["default_pricelist"])
            if not warehouse or not default_pricelist:
                continue

            # One cash payment method per till, never shared between them.
            payment_methods = self._ensure_pos_payment_method(company, spec["shop"])

            config = Config.search(
                [("name", "=", spec["name"]), ("company_id", "=", company.id)], limit=1
            )
            picking_type = warehouse.out_type_id
            values = {
                "name": spec["name"],
                "company_id": company.id,
                # This is what makes the sale decrement THIS shop's stock.
                "picking_type_id": picking_type.id,
                "use_pricelist": True,
                "pricelist_id": default_pricelist.id,
                "available_pricelist_ids": [(6, 0, available)],
            }
            if payment_methods:
                values["payment_method_ids"] = [(6, 0, payment_methods.ids)]

            if config:
                # Odoo refuses to change several pos.config fields while a
                # session is open, and rightly so - the till is mid-shift.
                # Re-seeding is documented as safe to re-run, so write only
                # what actually differs and never fail on a live till.
                changed = self._changed_values(config, values)
                if changed:
                    try:
                        config.write(changed)
                    except UserError as exc:
                        _logger.warning(
                            "POS %s not updated (%s): %s",
                            spec["name"],
                            ", ".join(sorted(changed)),
                            exc,
                        )
            else:
                config = Config.create(values)
            _logger.info(
                "pos: %s -> %s stock, default pricelist %s",
                spec["name"],
                warehouse.code,
                default_pricelist.name,
            )
        return True

    @api.model
    def _changed_values(self, record, values):
        """Subset of ``values`` that actually differs from ``record``.

        Idempotent seeding means a second run writes nothing at all - not
        merely that it produces the same end state. Several models (pos.config
        being the strict one) reject writes that would otherwise be no-ops.
        """
        changed = {}
        for key, value in values.items():
            field = record._fields.get(key)
            if field is None:
                continue
            if field.type in ("many2many", "one2many"):
                # Only the (6, 0, ids) "set" command is used by this seeder.
                if value and value[0][0] == 6 and set(record[key].ids) == set(value[0][2]):
                    continue
            elif field.type == "many2one":
                if record[key].id == value:
                    continue
            elif record[key] == value:
                continue
            changed[key] = value
        return changed

    @api.model
    def _ensure_pos_payment_method(self, company, shop_code):
        """One cash payment method per point of sale.

        Odoo forbids sharing a cash payment method between tills
        (``pos.config._check_payment_method_ids_journal``), and rightly so:
        each shop reconciles its own cash drawer, so each needs its own
        payment method backed by its own cash journal.
        """
        Method = self.env["pos.payment.method"]
        name = f"Cash ({shop_code})"
        method = Method.search(
            [("name", "=", name), ("company_id", "=", company.id)], limit=1
        )
        if method:
            return method

        journal = self._ensure_cash_journal(company, shop_code)
        if not journal:
            _logger.warning("no cash journal for %s; POS payment method not created", shop_code)
            return Method

        method = Method.create(
            {"name": name, "company_id": company.id, "journal_id": journal.id}
        )
        _logger.info("pos: payment method %s on journal %s", name, journal.code)
        return method

    @api.model
    def _ensure_cash_journal(self, company, shop_code):
        Journal = self.env["account.journal"]
        # Journal codes are short and must be unique per company.
        code = f"CSH{shop_code[-1]}" if shop_code[-1].isdigit() else f"C{shop_code[:4]}"
        journal = Journal.search(
            [("code", "=", code), ("company_id", "=", company.id)], limit=1
        )
        if journal:
            return journal
        return Journal.create(
            {
                "name": f"Cash {shop_code}",
                "type": "cash",
                "code": code,
                "company_id": company.id,
            }
        )

    # ==================================================================
    # Website / e-commerce (only if website_sale is installed)
    # ==================================================================
    @api.model
    def _seed_website(self, templates):
        if "website" not in self.env:
            _logger.info("website not installed; skipping e-commerce configuration")
            return False
        published = 0
        for template in templates.values():
            if "is_published" in template._fields and not template.is_published:
                template.is_published = True
                published += 1
        _logger.info("website: published %s product templates", published)
        return True

    # ==================================================================
    # Demo helper actions
    # ==================================================================
    @api.model
    def action_seed(self):
        self.seed_all()
        return self._notify(_("Demo data seeded."))

    @api.model
    def action_reset_demo(self):
        """Return to the seeded opening state.

        Closes POS sessions, cancels demo purchase orders and open transfers,
        re-applies the opening inventory and restores the mock providers.

        Fiscal transactions are deliberately NOT deleted: destroying a
        registration history is exactly what a fiscal system must never do.
        """
        company = self.env.company

        # Closing a session is a two-step dance and the second step complains if
        # the first already finished it. Judge by the end state, not by whether
        # a call raised - a demo reset that prints warnings it does not mean
        # undermines confidence in the demo.
        for session in self.env["pos.session"].search([("state", "!=", "closed")]):
            for step in ("action_pos_session_closing_control", "action_pos_session_close"):
                if session.state == "closed":
                    break
                try:
                    getattr(session, step)()
                except Exception:  # noqa: BLE001
                    pass
            session.invalidate_recordset(["state"])
            if session.state != "closed":
                _logger.warning(
                    "POS session %s is still %s; close it from the UI", session.name, session.state
                )

        for order in self.env["purchase.order"].search(
            [("state", "in", ("draft", "sent", "to approve", "purchase"))]
        ):
            # A purchase whose goods actually arrived cannot be cancelled, and
            # should not be: the receipt is real history. The opening stock is
            # restored by the inventory adjustment below regardless.
            if any(picking.state == "done" for picking in order.picking_ids):
                _logger.info("purchase order %s was received; leaving it as history", order.name)
                continue
            try:
                order.button_cancel()
            except Exception:  # noqa: BLE001
                _logger.warning("could not cancel purchase order %s", order.name)

        for picking in self.env["stock.picking"].search([("state", "not in", ("done", "cancel"))]):
            try:
                picking.action_cancel()
            except Exception:  # noqa: BLE001
                _logger.warning("could not cancel picking %s", picking.name)

        shops = {
            warehouse.code: warehouse
            for warehouse in self.env["stock.warehouse"].search([("company_id", "=", company.id)])
        }
        self._seed_opening_stock(company, shops)
        self._set_fiscal_failure_mode(False)
        return self._notify(_("Demo reset to the seeded opening state."))

    @api.model
    def action_toggle_fiscal_failure(self):
        """Flip the mock fiscal provider between working and failing."""
        current = self._get_fiscal_failure_mode()
        target = not current
        self._set_fiscal_failure_mode(target)
        return self._notify(
            _("Mock fiscal provider failure mode is now %s.") % ("ON" if target else "OFF")
        )

    # ==================================================================
    # Gateway control / status (used by the integration status screen)
    # ==================================================================
    @api.model
    def _gateway_config(self):
        return self.env["et.fiscal.gateway.client"]._get_config()

    @api.model
    def _gateway_request(self, method, path, payload=None):
        config = self._gateway_config()
        headers = {"X-API-Key": config["api_key"], "Content-Type": "application/json"}
        response = requests.request(
            method,
            f"{config['base_url']}{path}",
            data=json.dumps(payload) if payload is not None else None,
            headers=headers,
            timeout=config["timeout"],
        )
        response.raise_for_status()
        return response.json()

    @api.model
    def _get_fiscal_failure_mode(self):
        try:
            return bool(
                self._gateway_request("GET", "/api/v1/admin/mock/fiscal/failure-mode").get(
                    "failure_mode"
                )
            )
        except Exception:  # noqa: BLE001
            return False

    @api.model
    def _set_fiscal_failure_mode(self, enabled):
        try:
            self._gateway_request(
                "POST", "/api/v1/admin/mock/fiscal/failure-mode", {"enabled": bool(enabled)}
            )
            return True
        except Exception:  # noqa: BLE001
            _logger.warning("could not reach the gateway to set fiscal failure mode")
            return False

    # ==================================================================
    # Introspection used by the demo script and verification
    # ==================================================================
    @api.model
    def demo_snapshot(self):
        """Current stock and prices for the demo SKUs, as plain data."""
        company = self.env.company
        shops = {
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
                for code, warehouse in sorted(shops.items())
            }

        prices = {}
        demo_variant = self.env["product.product"].search(
            [("default_code", "=", DEMO_PURCHASE["sku"])], limit=1
        )
        if demo_variant:
            for key, spec in PRICELISTS.items():
                pricelist = self.env["product.pricelist"].search(
                    [("name", "=", spec["name"])], limit=1
                )
                if pricelist:
                    prices[key] = pricelist._get_product_price(demo_variant, 1.0)

        return {"stock": stock, "prices": prices, "sku": DEMO_PURCHASE["sku"]}

    @api.model
    def _notify(self, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Mati Demo"),
                "message": message,
                "sticky": False,
            },
        }
