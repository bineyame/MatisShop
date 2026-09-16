{
    "name": "External Delivery Gateway Connector",
    "version": "18.0.1.0.0",
    "category": "Inventory/Delivery",
    "summary": "Odoo delivery carrier backed by the Integration Gateway (KLIK, ...)",
    "description": """
External Delivery Gateway Connector
===================================

Adds a delivery carrier type - "Integration Gateway" - that books deliveries
through the Integration Gateway instead of calling a courier API directly.

Uses Odoo's native ``delivery`` framework: an ordinary ``delivery.carrier``
implementing the standard rate / send / track / cancel hooks, and courier
state stored on ``stock.picking`` where the warehouse can see it.

Which courier is behind it is a gateway configuration value
(``DELIVERY_PROVIDER``).

.. warning::
   The bundled courier is a MOCK. No courier is dispatched.
""",
    "author": "Mati Retail Platform",
    "license": "LGPL-3",
    "depends": ["delivery", "stock"],
    "data": [
        "data/delivery_carrier_data.xml",
        "views/delivery_carrier_views.xml",
        "views/stock_picking_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
