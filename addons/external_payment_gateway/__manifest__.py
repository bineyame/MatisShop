{
    "name": "External Payment Gateway Connector",
    "version": "18.0.1.0.0",
    "category": "Accounting/Payment Providers",
    "summary": "Odoo payment provider backed by the Integration Gateway (ArifPay, Chapa, Telebirr, ...)",
    "description": """
External Payment Gateway Connector
==================================

Adds one Odoo payment provider - "Integration Gateway" - that routes payments
through the Integration Gateway instead of talking to a payment rail directly.

Which rail is actually used is a gateway configuration value
(``PAYMENT_PROVIDER``). Odoo never learns the difference, so adding ArifPay,
Chapa or Telebirr later needs no change in this module.

This module deliberately plugs into Odoo's NATIVE payment framework
(``payment.provider`` / ``payment.transaction``) rather than inventing a
parallel payment system.

.. warning::
   The bundled gateway provider is a MOCK. No real funds move.
""",
    "author": "Mati Retail Platform",
    "license": "LGPL-3",
    "depends": ["payment", "account"],
    "data": [
        "data/payment_provider_data.xml",
        "views/payment_provider_views.xml",
        "views/payment_transaction_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
