{
    "name": "Mati's Shoes Demo Configuration",
    "version": "18.0.1.0.0",
    "category": "Sales",
    "summary": "Client-specific demo configuration and seed data for Mati's Shoes",
    "description": """
Mati's Shoes Demo Configuration
===============================

The CLIENT-SPECIFIC layer. It contains configuration and demo records only:

* company identity (name, TIN, ETB currency);
* supplier and a wholesale customer;
* Color/Size attributes, two shoe models, eight variants with deterministic
  SKUs and valid EAN-13 barcodes;
* retail / online / wholesale pricelists plus a quantity break;
* three warehouses with different opening stock per location;
* two POS configurations, each bound to its own shop stock and pricelist;
* demo helper actions used while presenting.

What is deliberately NOT here
-----------------------------
Generic Ethiopian fiscal, payment or delivery logic. That is reusable
infrastructure and lives in et_fiscal_odoo, external_payment_gateway and
external_delivery_gateway. Another Ethiopian retailer installs those three and
writes their own equivalent of this module.

There is no retail business logic in here at all: no custom inventory, no
custom pricing engine, no custom purchasing. Everything is vanilla Odoo,
configured.
""",
    "author": "Mati Retail Platform",
    "license": "LGPL-3",
    "depends": [
        "base",
        "product",
        "stock",
        "purchase",
        "sale_management",
        "point_of_sale",
        "account",
        "website_sale",
        "et_fiscal_odoo",
        "external_payment_gateway",
        "external_delivery_gateway",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/mati_demo_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": True,
    "auto_install": False,
}
