# Providers

How the three external rails are selected, configured and swapped.

> **Every bundled provider is a mock.** Nothing in this platform currently
> talks to a real payment rail, courier or fiscal authority.

## The rule

Switching a rail is **configuration plus credentials**. It is never a code
change in Odoo, never a database migration, and never a change to any business
workflow.

```
Odoo payment/delivery/fiscal record
        │   (unchanged, whichever provider is active)
        ▼
Odoo adapter addon
        │   normalized contract
        ▼
Integration Gateway
        │   provider registry
        ▼
   mock  |  provider_a  |  provider_b
```

If you ever find yourself writing `if provider == "a": ... elif provider ==
"b": ...` outside an adapter, the difference belongs **inside** that adapter.

## Selecting a provider

Three environment variables on the **gateway** service:

```bash
FISCAL_PROVIDER=mock
PAYMENT_PROVIDER=mock
DELIVERY_PROVIDER=mock
```

The effective values are readable at runtime:

- `GET /health` on the gateway returns `providers: {fiscal, payment, delivery}`
- Odoo: **Mati Demo → Integration Status** shows the same three, read-only

Neither ever shows a credential.

## Fail fast

Selecting a real provider without its credentials **stops the gateway from
starting**:

```
RuntimeError: invalid provider configuration:
  - PAYMENT_PROVIDER='provider_a': payment provider 'provider_a' is selected
    but not configured: missing PAYMENT_PROVIDER_A_BASE_URL, ...
```

This is deliberate. A gateway that booted and quietly fell back to a mock
would accept payments it cannot take and "register" fiscal documents that
reach no authority — and the operator would find out from a customer rather
than from a deploy.

Mocks are the explicit default in `.env.example`. Choosing a real provider is
always a deliberate act.

---

## Payment rail

| | |
|---|---|
| Contract | `gateway/app/providers/payment/base.py` |
| Operations | `create_payment`, `verify_payment`, `refund` |
| Odoo side | `external_payment_gateway` — one `payment.provider`, code `integration_gateway` |

### `mock` — implemented, default

| | |
|---|---|
| Class | `app/providers/payment/mock.py::MockPaymentProvider` |
| Required settings | none |
| Status | **fully functional**, no real funds move |
| Behaviour | `MOCK_PAYMENT_MODE` = `auto_success` (default), `manual`, `always_fail` |
| Limitations | provider ids are derived from the idempotency key; no real checkout page |

### `provider_a` — adapter seam, not implemented

| | |
|---|---|
| Class | `app/providers/payment/provider_a.py::ProviderAPaymentProvider` |
| Required settings | `PAYMENT_PROVIDER_A_BASE_URL`, `PAYMENT_PROVIDER_A_API_KEY`, `PAYMENT_PROVIDER_A_MERCHANT_ID` |
| Status | **seam only** — fails loudly on selection |
| Blocking | the provider's API document is not in this repository; sandbox credentials; webhook signature scheme; whether it deduplicates on an idempotency key |

### `provider_b` — adapter seam, not implemented

| | |
|---|---|
| Class | `app/providers/payment/provider_b.py::ProviderBPaymentProvider` |
| Required settings | `PAYMENT_PROVIDER_B_BASE_URL`, `PAYMENT_PROVIDER_B_API_KEY` |
| Status | **seam only** |
| Blocking | as above |

> `provider_a` / `provider_b` are placeholder labels, not products. Rename the
> module, the class and the registry key to the provider's real name when its
> API document arrives.

---

## Delivery rail

| | |
|---|---|
| Contract | `gateway/app/providers/delivery/base.py` |
| Operations | `create_delivery`, `get_status`, `cancel_delivery` |
| Odoo side | `external_delivery_gateway` — `delivery.carrier`, type `integration_gateway` |
| Lifecycle | `created → assigned → picked_up → in_transit → delivered`, plus `failed` / `cancelled` |

### `mock` — implemented, default

| | |
|---|---|
| Class | `app/providers/delivery/mock.py::MockDeliveryProvider` |
| Required settings | none |
| Status | **fully functional**, no courier is dispatched |
| Behaviour | `MOCK_DELIVERY_MODE` = `auto_advance` (default), `manual`, `always_fail` |

### `provider_a` — adapter seam, not implemented

| | |
|---|---|
| Class | `app/providers/delivery/provider_a.py::ProviderADeliveryProvider` |
| Required settings | `DELIVERY_PROVIDER_A_BASE_URL`, `DELIVERY_PROVIDER_A_API_KEY`, `DELIVERY_PROVIDER_A_MERCHANT_ID` |
| Status | **seam only** |
| Blocking | API document not supplied; sandbox credentials; coverage and pricing rules; status callback contract |

---

## Fiscal rail

| | |
|---|---|
| Contract | `gateway/app/providers/fiscal/base.py` |
| Operations | `register`, `get_status`, `cancel` |
| Odoo side | `et_fiscal_odoo` — `et.fiscal.transaction` |

### `mock` — implemented, default

| | |
|---|---|
| Class | `app/providers/fiscal/mock.py::MockFiscalProvider` |
| Required settings | none |
| Status | **fully functional**, produces **no legally valid registration** |
| Behaviour | own registration ledger keyed by idempotency key; IRN `ET-DEMO-YYYY-NNNNNN`; base64 QR payload; `MOCK_FISCAL_FAILURE_MODE` to force failures |
| Limitations | the IRN format and QR layout are **invented for the demo** |

### `mor` / `accredited` — adapter seams, not implemented

| | |
|---|---|
| Classes | `app/providers/fiscal/placeholders.py` |
| Required settings | `mor`: MoR endpoint, client id/secret, client certificate, taxpayer TIN. `accredited`: `FISCAL_PROVIDER_BASE_URL`, `FISCAL_PROVIDER_API_KEY`, `FISCAL_PROVIDER_DEVICE_ID` |
| Status | **seam only** |
| Blocking | authoritative MoR technical specification or an accredited-provider contract; accreditation; production credentials; digital certificate; the real IRN and QR formats; the official offline rules |

---

## Worked examples

**Today — everything on mocks (this is `.env.example`):**

```bash
FISCAL_PROVIDER=mock
PAYMENT_PROVIDER=mock
DELIVERY_PROVIDER=mock
```

**Later — one real payment rail, everything else still mocked:**

```bash
PAYMENT_PROVIDER=provider_a
PAYMENT_PROVIDER_A_BASE_URL=https://api.provider-a.example
PAYMENT_PROVIDER_A_API_KEY=...
PAYMENT_PROVIDER_A_MERCHANT_ID=...

FISCAL_PROVIDER=mock
DELIVERY_PROVIDER=mock
```

then `docker compose restart gateway`. Odoo is untouched.

**Rolling back** is the same move in reverse — set `PAYMENT_PROVIDER=mock` and
restart. Nothing is reinstalled and no Odoo module changes.

---

## Adding a real provider

1. **Put the API document in the repository** and read it. Keep its
   terminology.
2. Implement the contract in the rail's adapter module. The field mapping
   between the provider's schema and the normalized contract belongs **here** —
   never in Odoo.
3. **Honour `idempotency_key`.** If the provider has no idempotency mechanism,
   deduplicate locally *and raise it as a risk* — the platform's
   exactly-once guarantee depends on this layer.
4. **Classify errors** as `ProviderTransientError` (retry) or
   `ProviderPermanentError` (do not), so the existing retry logic keeps
   working unchanged.
5. **Implement `verify_*` properly.** The gateway calls it before believing
   any webhook; a webhook is a hint, not proof.
6. Override the webhook signature check for that provider's scheme.
7. Add the class to the registry in `app/providers/registry.py`, keeping the
   registry key equal to the class's `name`.
8. Write the field-by-field mapping to `docs/providers/<rail>-<provider>.md`,
   including anything the provider does that the normalized model cannot
   express.
9. Add tests. `gateway/tests/test_provider_configuration.py` already asserts
   the registry rules; add behaviour tests for the adapter itself.

### Where credentials live

**Only in the gateway environment.** Never in Odoo source, never in
`mati_demo`, never in the database, never in a log (payloads are redacted
before they reach the audit tables), and never in the Integration Status
screen.

Settings are rail-scoped — `PAYMENT_PROVIDER_A_API_KEY` is not readable as a
delivery credential — so a mistake in one adapter cannot leak another rail's
secret.
