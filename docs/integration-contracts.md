# Integration contracts

The wire contracts between an ERP and the Integration Gateway, and how to add a
real provider behind them.

Authoritative definitions live in `gateway/app/domain/contracts.py`. Live,
browsable OpenAPI: <http://localhost:8000/docs>.
Worked examples: `docs/contracts/*.json`, replayed against the real gateway by
`tests/contract/`.

## Conventions

| Convention | Rule | Why |
|---|---|---|
| **Money** | decimal **strings** with two places (`"6199.99"`) | JSON floats cannot represent 6199.99 exactly; a fiscal total off by a cent is a rejected document. Numbers are *accepted* on input and coerced; responses always emit strings. |
| **Idempotency** | every mutating call carries `idempotency_key` | the caller's stable logical identity for the operation |
| **Authentication** | `X-API-Key` header on everything under `/api/v1` | health endpoints are public; webhooks authenticate by signature instead |
| **Correlation** | `X-Correlation-Id` accepted and echoed | ties an ERP action to every gateway log line and audit row |
| **Provider override** | `X-Provider` header | for testing a specific provider without changing configuration |
| **Unknown fields** | rejected (`extra="forbid"`) | a typo in a field name must fail loudly, not be silently dropped |

### HTTP status codes

| Code | Meaning |
|---|---|
| `200` | the request was accepted and durably recorded — **read the domain `status`** |
| `401` | missing/invalid `X-API-Key`, or invalid webhook signature |
| `404` | no such resource |
| `409` | idempotency conflict, operation already in progress, or an invalid state transition |
| `422` | the request body is invalid, or a provider permanently rejected it |
| `501` | the configured provider is an unimplemented extension point |
| `502` / `503` | transport-level provider failure surfaced directly |

**The important one:** a provider failure is *not* an HTTP error. If the mock
fiscal provider is down, the gateway still answers `200`:

```json
{"id": "...", "status": "failed", "irn": null,
 "last_error": "mock fiscal provider is in forced failure mode", "attempt_count": 3}
```

The gateway *did* its job — it accepted, recorded and attempted the request.
The ERP reads `status`, keeps its own record retryable, and tries again later
with the same key. This is what makes offline recovery simple: there is exactly
one code path, whether the provider was up or down.

---

## Fiscal rail

### `POST /api/v1/fiscal/documents`

Register one fiscal document. Repeating an identical request returns the
original registration with `replayed: true` and never creates a second IRN.

```jsonc
{
  "idempotency_key": "6f1c2d4e8a9b4c3d8e7f0a1b2c3d4e5f",
  "source":   { "system": "odoo", "model": "pos.order",
                "record_id": "42", "reference": "Shop 1 Retail/0001" },
  "document": { "type": "receipt", "number": "Shop 1 Retail/0001",
                "issued_at": "2026-03-14T09:31:22+00:00" },
  "seller":   { "name": "Mati's Shoes PLC", "tin": "0012345678",
                "address": "Bole Road, Addis Ababa", "phone": "+251 11 000 0000" },
  "buyer":    { "name": "Walk-in Customer" },
  "lines": [{
    "line_id": "1", "sku": "SAM-BLK-42", "barcode": "2000004200008",
    "description": "Adidas Samba (Black, 42)",
    "quantity": "1.0", "unit_price": "6000.00", "discount": "0.00",
    "tax_code": "VAT 15%", "tax_rate": "15.0",
    "tax_amount": "900.00", "line_total": "6000.00"
  }],
  "taxes": [{ "code": "VAT15", "name": "VAT 15%", "rate": "15.0",
              "base": "6000.00", "amount": "900.00" }],
  "totals": { "currency": "ETB", "subtotal": "6000.00",
              "tax_total": "900.00", "grand_total": "6900.00" }
}
```

Response:

```json
{
  "id": "0b8c1f...", "idempotency_key": "6f1c2d...",
  "status": "registered", "provider": "mock",
  "provider_transaction_id": "fp_00000001",
  "irn": "ET-DEMO-2026-000001",
  "qr_payload": "RVRERU1PMXxFVC1ERU1PLTIwMjYt...",
  "grand_total": "6900.00", "attempt_count": 1,
  "registered_at": "2026-03-14T09:31:23.114Z",
  "correlation_id": "...", "replayed": false
}
```

Server-side validation the ERP must satisfy:

- at least one line;
- `sum(lines[].line_total)` equals `totals.subtotal` (±0.05);
- `subtotal + tax_total` equals `grand_total` (±0.05);
- the mock provider additionally requires a seller TIN and a positive total.

### Other fiscal endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/fiscal/documents/{id}` | read one document |
| `GET /api/v1/fiscal/documents/by-key/{idempotency_key}` | recovery: the ERP lost the id but kept its key |
| `POST /api/v1/fiscal/documents/{id}/retry` | re-submit under the original identity |
| `POST /api/v1/fiscal/documents/{id}/cancel` | cancel at the provider |

---

## Payment rail

### `POST /api/v1/payments`

```jsonc
{
  "idempotency_key": "odoo-payment-S00023-1",
  "reference": "S00023-1",
  "source": { "system": "odoo", "model": "payment.transaction", "record_id": "7" },
  "amount": "6200.00",
  "currency": "ETB",
  "customer": { "name": "Abebe Kebede", "phone": "+251911223344" },
  "return_url": "http://localhost:8069/payment/status",
  "metadata": { "odoo_transaction_id": 7 }
}
```

Statuses: `pending → authorized → succeeded`, plus `failed`, `cancelled`,
`partially_refunded`, `refunded`.

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/payments/{id}` | read one payment |
| `GET /api/v1/payments/by-reference/{reference}` | what Odoo polls with its own reference |
| `POST /api/v1/payments/{id}/verify` | authoritative re-read from the provider |
| `POST /api/v1/payments/{id}/refund` | full or partial refund |

**Never trust a webhook alone for money.** A payment webhook triggers a
`verify` against the provider before any state change is applied.

---

## Delivery rail

### `POST /api/v1/deliveries`

```jsonc
{
  "idempotency_key": "odoo-delivery-WH/OUT/00012",
  "reference": "WH/OUT/00012",
  "source": { "system": "odoo", "model": "stock.picking", "record_id": "12" },
  "pickup":  { "name": "Mati Main Warehouse", "city": "Addis Ababa", "country_code": "ET" },
  "dropoff": { "name": "Abebe Kebede", "city": "Addis Ababa", "country_code": "ET" },
  "packages": [{ "description": "Adidas Samba (Black, 42)",
                 "quantity": "1.0", "sku": "SAM-BLK-42" }],
  "cash_on_delivery": "0.00",
  "currency": "ETB"
}
```

Normalized lifecycle — every courier adapter maps its own vocabulary onto these:

```
created -> assigned -> picked_up -> in_transit -> delivered
      \-> cancelled                          \-> failed
```

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/deliveries/{id}` | read one delivery |
| `POST /api/v1/deliveries/{id}/refresh` | poll the courier |
| `POST /api/v1/deliveries/{id}/cancel` | cancel |

---

## Webhooks

### `POST /api/v1/webhooks/{rail}/{provider}`

Authenticated by signature, not by API key — providers do not have one.

```
X-Signature: sha256=<hmac_sha256(GATEWAY_WEBHOOK_SECRET, raw_body)>
```

Rules, in order:

1. the signature is verified over the **raw bytes**, before parsing;
2. the callback is recorded either way — a rejected one with
   `signature_valid=false` and no payload retained, because probing attempts
   are exactly what you want logged;
3. duplicates are ignored via `UNIQUE (provider, external_event_id)`;
4. for money, the callback triggers a `verify` rather than being applied.

---

## Adding a real provider

The whole point of this boundary: adding a provider is **one class plus one
registry line**. No Odoo change, no database migration, no contract change.

### Adding a real fiscal provider

1. Implement `app/providers/fiscal/base.py::FiscalProvider`:

   ```python
   class MyFiscalProvider(FiscalProvider):
       name = "myprovider"

       def __init__(self, settings, session):
           self.settings, self.session = settings, session

       async def register(self, document, *, attempt=1) -> FiscalRegistrationResult: ...
       async def cancel(self, reference, reason=None) -> FiscalCancellationResult: ...
       async def get_status(self, reference) -> FiscalStatusResult: ...
   ```

2. **Honour the idempotency key.** `document.idempotency_key` is the logical
   identity. If the provider supports an idempotency header, pass it through.
   If it does not, deduplicate locally before calling — and treat that gap as a
   finding worth raising.

3. **Classify errors correctly.** This is what keeps the existing retry logic
   working:

   | Provider says | Raise | Result |
   |---|---|---|
   | timeout, connection reset, 5xx, rate limit | `ProviderTransientError` | retried with backoff |
   | rejected, invalid document, bad credentials | `ProviderPermanentError` | not retried |
   | not configured | `ProviderNotConfigured` | surfaced as 501 |

4. **Keep credentials in the environment.** Read them from `Settings`; never
   hardcode, never accept them from the ERP.

5. Register it:

   ```python
   # app/providers/registry.py
   FISCAL_PROVIDERS = {"mock": MockFiscalProvider, "myprovider": MyFiscalProvider}
   ```

6. Switch to it: `FISCAL_PROVIDER=myprovider`, restart the gateway. Odoo is
   untouched.

7. Sign the QR payload in the provider, not in Odoo — that is where the
   certificate lives.

### Adding a real payment provider

Same shape against `PaymentProvider`. Additionally:

- override the webhook signature check for that provider's scheme (some sign a
  canonical string; some include a timestamp to defeat replay);
- always implement `verify_payment` properly — the gateway calls it before
  believing any callback.

Stubs with their required settings and outstanding blockers already exist:
`ArifPayProvider`, `ChapaProvider`, `TelebirrProvider`.

### Adding a real delivery provider

Same shape against `DeliveryProvider`. Map the courier's status vocabulary onto
the seven normalized states. `KlikProvider` is the stub.

---

## Why the placeholders raise instead of pretending

`MoRFiscalProvider`, `ArifPayProvider`, `ChapaProvider`, `TelebirrProvider` and
`KlikProvider` all raise `ProviderNotConfigured` with a structured detail:

```json
{
  "code": "provider_not_configured",
  "message": "fiscal provider 'mor' is an extension point and is not implemented",
  "detail": {
    "required_settings": ["MOR_FISCAL_BASE_URL", "MOR_FISCAL_CLIENT_ID", "..."],
    "blockers": ["authoritative MoR technical API specification",
                 "taxpayer enrolment and production credentials", "..."]
  }
}
```

We do not have authoritative, current API specifications for these systems.
A plausible-looking fake would pass review and fail in production, which is
strictly worse than an explicit, documented gap. The stubs say exactly what is
needed and what is blocking, and `gateway/tests/test_providers.py` asserts they
keep saying it.
