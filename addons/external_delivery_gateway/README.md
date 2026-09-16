# External Delivery Gateway Connector (`external_delivery_gateway`)

Adds a delivery carrier type - **Integration Gateway** - that books deliveries
through the gateway instead of calling a courier API directly.

Uses Odoo's native `delivery` framework: an ordinary `delivery.carrier`
implementing `rate_shipment`, `send_shipping`, `get_tracking_link` and
`cancel_shipment`. Courier state lands on `stock.picking`, where the warehouse
can see it.

## Normalized lifecycle

```
created -> assigned -> picked_up -> in_transit -> delivered
      \-> cancelled                          \-> failed
```

Every courier adapter maps its own vocabulary onto these seven states, so Odoo
sees one lifecycle regardless of who is delivering.

## Swapping the courier

```
DELIVERY_PROVIDER=mock   # bundled demo courier
DELIVERY_PROVIDER=klik   # extension point, not implemented
```

## Warning

The bundled courier is a mock. No courier is dispatched.
