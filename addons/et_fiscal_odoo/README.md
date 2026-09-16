# Ethiopian Fiscalization Connector (`et_fiscal_odoo`)

Reusable Odoo 18 adapter for Ethiopian fiscal registration. Not specific to any
retailer: client configuration belongs in a separate module.

## What it owns

| Concern | Here? |
|---|---|
| Fiscal document lifecycle in Odoo | yes |
| Mapping Odoo records to the normalized contract | yes |
| Storing IRN / QR / provider reference on Odoo records | yes |
| Retry scheduling from Odoo's side | yes |
| Provider REST APIs | no - gateway |
| Provider credentials and signing | no - gateway |
| Retry/backoff against the provider | no - gateway |
| Webhook verification | no - gateway |

## Models

- `et.fiscal.transaction` - one fiscal document, with an explicit state
  machine and an immutable `idempotency_key`.
- `et.fiscal.document.mixin` - implemented by `pos.order` and `account.move`;
  implement `_prepare_fiscal_document()` to fiscalize anything else.
- `et.fiscal.gateway.client` - the only place in Odoo that speaks HTTP to the
  gateway.

## Configuration

Settings > Ethiopian Fiscalization. The gateway URL and API key are seeded from
`GATEWAY_BASE_URL` / `GATEWAY_API_KEY` at install time.

The company TIN lives on `res.company` (Settings > Companies), because it is a
property of the legal entity rather than of this module.

## Tests

```
docker compose run --rm odoo odoo -d odoo --test-enable \
    --test-tags /et_fiscal_odoo --stop-after-init
```

## Warning

The bundled demo registers against a **mock** provider. Nothing it produces is
a legally valid Ethiopian fiscal registration.
