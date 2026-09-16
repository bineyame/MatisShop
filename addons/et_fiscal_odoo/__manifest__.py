{
    "name": "Ethiopian Fiscalization Connector",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "summary": "Fiscal registration of POS orders and invoices through the Integration Gateway",
    "description": """
Ethiopian Fiscalization Connector
=================================

Reusable Odoo adapter for Ethiopian fiscal registration. It is NOT specific to
any single retailer - client-specific configuration belongs in a separate
module (see ``mati_demo`` for an example).

What this module does
---------------------
* adds ``et.fiscal.transaction``: an explicit fiscal document record with its
  own state machine, stable idempotency key and full attempt history;
* maps Odoo POS orders and customer invoices onto the normalized gateway
  contract;
* submits them to the Integration Gateway and stores the result (IRN, QR
  payload, provider reference) against the source record;
* retries failed submissions without ever creating a second registration;
* renders a fiscal receipt/invoice block showing status, IRN and QR code.

What this module deliberately does NOT do
-----------------------------------------
* speak any provider's REST API;
* hold provider credentials;
* implement provider authentication, signing or retry engines.

All of that lives in the Integration Gateway, behind a stable contract. See
docs/architecture.md and docs/fiscal-integration.md.

.. warning::
   The bundled demo path registers against a MOCK provider. Nothing produced
   is a legally valid Ethiopian fiscal registration.
""",
    "author": "Mati Retail Platform",
    "license": "LGPL-3",
    "depends": [
        "base",
        "base_setup",
        "mail",
        "account",
        "point_of_sale",
    ],
    "data": [
        "security/et_fiscal_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence.xml",
        "data/ir_cron.xml",
        "views/et_fiscal_transaction_views.xml",
        "views/res_config_settings_views.xml",
        "views/res_company_views.xml",
        "views/pos_order_views.xml",
        "views/account_move_views.xml",
        "report/et_fiscal_receipt_report.xml",
        "report/account_move_fiscal_block.xml",
        "views/menus.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
    "auto_install": False,
}
