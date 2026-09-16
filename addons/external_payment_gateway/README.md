# External Payment Gateway Connector (`external_payment_gateway`)

Adds one Odoo payment provider - **Integration Gateway** - that routes payments
through the gateway rather than talking to a payment rail directly.

It uses Odoo's native payment framework: ordinary `payment.provider` and
`payment.transaction` records, ordinary Odoo checkout flows. No parallel
payment system.

## Swapping the rail

Odoo needs no change. Set `PAYMENT_PROVIDER` in the gateway environment:

```
PAYMENT_PROVIDER=mock      # bundled demo provider
PAYMENT_PROVIDER=arifpay   # extension point, not implemented
PAYMENT_PROVIDER=chapa     # extension point, not implemented
PAYMENT_PROVIDER=telebirr  # extension point, not implemented
```

## Notification flow

The reference flow is **polling**: `action_gateway_poll_status()` asks the
gateway for authoritative state. That needs no inbound network path into Odoo.

An optional push endpoint exists at
`/payment/integration_gateway/notification`. It never trusts the payload - it
only learns which transaction to re-read from the gateway.

## Warning

The bundled provider is a mock. No real funds move.
