# Demo Readiness Report — Mati's Shoes

Status: **demo-ready.** Everything below was executed on a stack rebuilt from
wiped volumes, not asserted from reading code.

| Check | Result |
|---|---|
| Clean bootstrap (`down -v` → `up` → `bootstrap`) | **exit 0, first try** |
| End-to-end verification (`make verify`) | **112 checks, 0 failed** |
| Gateway + cross-boundary contract tests | **129 passed** (laptop and in-image) |
| Odoo addon test suites | **68 passed, 0 failed, 0 errors** |
| `make demo-reset` | restores exact opening stock, no spurious warnings |
| Service health | postgres, odoo, gateway all `ok` |

> **All external providers are mocks.** No IRN, QR code, payment or delivery
> produced here has any real-world validity. That is a deliberate boundary,
> not an unfinished edge.

---

## Existing implementation reviewed

The repository already contained a working platform, and it was **not** rebuilt:

- **Odoo 18 Community** pinned to commit `d60ab9c`, built from official source,
  core never modified. Verified: `import odoo` resolves to the pinned tree.
- **PostgreSQL** with two logical databases — `odoo` (business) and
  `integration_gateway` (integration). Neither side reaches into the other.
- **Docker Compose** topology with healthchecks on all three services.
- **Three reusable Odoo addons** — `et_fiscal_odoo` (fiscal transaction model,
  explicit state machine, immutable idempotency key, retry cron, QWeb fiscal
  receipt), `external_payment_gateway` (native `payment.provider`),
  `external_delivery_gateway` (native `delivery.carrier`).
- **Integration Gateway** (FastAPI) owning providers, credentials, retries,
  idempotency, webhook verification and an append-only audit trail.
- **Mock fiscal/payment/delivery providers** with injectable failure modes.
- **Tests and ADRs** — 110 gateway tests, 57 Odoo tests, 11 ADRs at the start.

What was **wrong for Mati** was the demo *content*, not the architecture: it
modelled a central warehouse, Adidas/Nike products, an Online pricelist and a
quantity-break rule — none of which match how he works.

---

## Changes made

### Domain model (the substantive work)

| Before | Now | Why |
|---|---|---|
| Main Warehouse + 2 shops | **2 shops only** | Mati has no central warehouse; stock goes supplier → shop |
| Adidas Samba / Nike Air Max | **Zala 2147, Rasdashen 218, Kangaroo C1** | realistic factory/model names |
| Product name only | **Factory + Model on the template** | a shoe has one factory; that identifies the model |
| Retail / Online / Wholesale + bulk break | **Retail + Wholesale** | the two prices Mati actually quotes |
| Price per template, bulk rule at 20+ | **one price per model, all sizes** | he prices by model, not by size |
| Transfer step in the demo | **removed** | there is no warehouse to transfer from |
| POS locked to one pricelist | **both pricelists on both tills** | a wholesale customer can walk into Shop 1 |

### New capability

- **`shoe_factory` / `shoe_model`** on `product.template`, with form fields,
  search fields and a *group by Factory* filter.
- **Integration Status screen** (`mati.integration.status`) — live provider
  names, gateway reachability, fiscal counts, failure-mode toggle. Reads
  providers back from the gateway's own `/health`; **never shows a secret**.
- **Demo navigation menu** — Demo Controls, Integration Status, Fiscal
  Transactions, Products, Stock by Shop, Purchase Orders.
- **`make demo-reset`** — re-seed, reset stock, close sessions, restore mocks.
- **`tools/normalize_mati_inventory.py`** — turns his flat sheet into
  templates / variants / prices / per-shop stock. **Refuses to guess**: a row
  without a shop is an error, and one model priced inconsistently across its
  variants is reported rather than silently resolved.

### Provider architecture

- **Rail-scoped credentials** (`PAYMENT_PROVIDER_A_*`, `DELIVERY_PROVIDER_A_*`,
  `FISCAL_PROVIDER_*`) so one rail's key cannot be read by another's adapter.
- **`provider_a` / `provider_b` adapter seams** in their own modules, with
  required settings and blockers declared. No endpoint or field name invented.
- **Fail-fast startup validation** — a real provider selected without
  credentials stops the gateway booting. It never silently falls back to a mock.
- Superseded speculative classes (ArifPay/Chapa/Telebirr/KLIK) removed; their
  outstanding blockers carried into `docs/providers.md`.

### Defects found by running it

| Defect | Impact had it shipped |
|---|---|
| Fiscal seams validated only when *called* | `FISCAL_PROVIDER=mor` would boot cleanly and fail at the first sale of the day |
| `demo-reset` printed warnings it did not mean | sessions close in two steps; the second complains the first finished. Reset *looked* broken |
| `related` fields on a non-searchable computed Many2one | Odoo warning on every start |
| Test read `.env.example` from a path absent in the image | guard passed on a laptop, failed in-container |

---

## Confirmed Mati model

```
Factory + Model        →  product.template        ("Zala 2147")
Color + Size           →  product.product         ("Zala 2147 / Black / 39")
Retail + Wholesale     →  two pricelists, one product identity
Quantity               →  stock at a shop location, not product master data
Supplier               →  delivers directly to a shop (no central warehouse)
One company / one TIN  →  two operational shops (NOT multi-company)
Internal barcodes      →  stored at variant level, generated externally
```

**Seeded, deterministic:**

| SKU | Barcode | Retail | Wholesale | Shop 1 | Shop 2 |
|---|---|---:|---:|---:|---:|
| `ZALA-2147-BLK-39` | `2000003900008` | 6,000 | 5,200 | **12** | 5 |
| `ZALA-2147-BLK-40` | `2000004000004` | 6,000 | 5,200 | 8 | 6 |
| `ZALA-2147-WHT-39` | `2000013900005` | 6,000 | 5,200 | 4 | 7 |
| `ZALA-2147-WHT-40` | `2000014000001` | 6,000 | 5,200 | 3 | 4 |
| `RASD-218-BRN-41` | `2000124100004` | 4,800 | 4,100 | 5 | 2 |
| `RASD-218-BRN-42` | `2000124200001` | 4,800 | 4,100 | 6 | 3 |
| `KANG-C1-BLK-40` | `2000204000002` | 5,500 | 4,700 | 9 | 2 |
| `KANG-C1-BLK-41` | `2000204100009` | 5,500 | 4,700 | 7 | 4 |

`ZALA-2147-BLK-39` opens at 12 in Shop 1 deliberately: the purchase demo adds
20 and the answer is exactly 32.

---

## Demo steps

**[`docs/demo-script.md`](docs/demo-script.md)** — Parts A–I, business
language, written to be read aloud.

Every number it quotes is asserted by **`make verify`**, which walks the same
path against the real database and the real gateway and exits non-zero if
anything is wrong. Observed on the last run:

```
D  PO destined for Shop 1; confirm leaves stock at 12; receipt → 32; Shop 2 untouched
E  Shop 1 POS @ 6,000 → 32 → 31; Shop 2 unaffected; sale attributable to Shop 1
F  same barcode, same product record @ 5,200 at Shop 2 → 5 → 4
G  fiscal registered, IRN + QR stored, re-submission returns the same IRN
I  provider down → sale completes, stock correct, fiscal failed + retryable
   → restored → retry registered → retry again same IRN → one registration
```

Rehearse with `make demo-reset` between runs.

---

## Provider switching

Today, and the committed default in `.env.example`:

```bash
FISCAL_PROVIDER=mock
PAYMENT_PROVIDER=mock
DELIVERY_PROVIDER=mock
```

Later, one real rail while the rest stay mocked:

```bash
PAYMENT_PROVIDER=provider_a
PAYMENT_PROVIDER_A_BASE_URL=https://api.provider-a.example
PAYMENT_PROVIDER_A_API_KEY=...
PAYMENT_PROVIDER_A_MERCHANT_ID=...
```

then `docker compose restart gateway`. **Odoo is untouched** — no module
reinstall, no migration, no change to products, purchasing, stock or tills.
Rolling back is the same move in reverse.

Misconfiguration is loud:

```
RuntimeError: invalid provider configuration:
  - PAYMENT_PROVIDER='provider_a': ... missing PAYMENT_PROVIDER_A_API_KEY
```

Full detail: [`docs/providers.md`](docs/providers.md).

---

## What is mocked

| Rail | Bundled | Behaviour | Real options |
|---|---|---|---|
| Fiscal | `MockFiscalProvider` | own ledger keyed by idempotency key; IRN `ET-DEMO-YYYY-NNNNNN`; base64 QR; injectable failure | `mor`, `accredited` — **seams only** |
| Payment | `MockPaymentProvider` | `auto_success` / `manual` / `always_fail` | `provider_a`, `provider_b` — **seams only** |
| Delivery | `MockDeliveryProvider` | seven-state lifecycle, advances on poll | `provider_a` — **seam only** |

The IRN format and QR layout are **invented for the demo**. Nothing here is
accredited, certified or legally valid.

---

## What is ready for real API integration

Each seam is a single class plus one registry line. Nothing else in the
platform moves.

| Rail | File | Required settings |
|---|---|---|
| Payment A | `gateway/app/providers/payment/provider_a.py` | `PAYMENT_PROVIDER_A_BASE_URL`, `_API_KEY`, `_MERCHANT_ID` |
| Payment B | `gateway/app/providers/payment/provider_b.py` | `PAYMENT_PROVIDER_B_BASE_URL`, `_API_KEY` |
| Delivery A | `gateway/app/providers/delivery/provider_a.py` | `DELIVERY_PROVIDER_A_BASE_URL`, `_API_KEY`, `_MERCHANT_ID` |
| Fiscal | `gateway/app/providers/fiscal/placeholders.py` | per provider; see `docs/providers.md` |

What an implementation must do, in order: map the provider's schema onto the
normalized contract **inside the adapter**; honour `idempotency_key`; classify
errors transient vs permanent so existing retry logic keeps working; implement
`verify_*` properly, because a webhook is a hint and not proof.

`provider_a` / `provider_b` are placeholder labels. Rename module, class and
registry key to the real product name when its document arrives.

---

## Remaining pilot blockers

Genuine blockers only.

**Fiscal — the long pole**
1. An authoritative MoR technical specification, or a contract with an
   accredited fiscal provider.
2. Accreditation of this integration; production credentials; taxpayer
   enrolment; the digital certificate for QR signing.
3. The real IRN and QR formats, and the official offline/store-and-forward
   rules. What exists here demonstrates the *architecture* for offline
   resilience, not Ethiopia's protocol.
4. **Confirmation that the chosen provider deduplicates on an idempotency
   key.** If it does not, escalate — no gateway code fully compensates.

**Payment / delivery**

5. The API documents for the two payment providers and the courier — they are
   not in this repository, so the adapters are seams.
6. Merchant accounts, sandbox then live credentials, confirmed webhook
   signature schemes, settlement and reconciliation rules.

**Platform**

7. A managed VPS with a **static outbound IP** (providers whitelist callers),
   HTTPS, a domain.
8. Backups with PITR on both databases plus the Odoo filestore, and a tested
   restore.
9. Production security: per-client gateway auth, secrets out of `.env`, TLS
   internally, least-privilege database roles.
10. Monitoring and alerting on `fiscal.failed` and retry-backlog depth.

**Known limitations, not blockers:** the POS receipt printed at sale time
carries no IRN (registration completes after the sale — a reprintable fiscal
receipt is provided instead); credit wholesale is vanilla Odoo with no
wholesale-specific UI; no gateway rate limiting; Odoo→gateway calls are
synchronous, so a hanging gateway adds latency at the till, mitigated by
setting `et_fiscal.auto_submit = False` and letting the cron carry it.

**Financial reporting by shop:** stock, sales and payments are attributable
per shop today via locations and `pos.order.config_id`. Per-shop
profitability and inventory valuation are *not* separated in Odoo Community
without analytic accounting — that is later work, and deliberately not faked.

---

## Recommended next step

**Implement one real provider — whichever API document arrives first.**

Everything else is refinement; this is the step that either validates the
boundary or exposes it while it is still cheap to move. Concretely:

1. put the API document in the repository and read it;
2. implement the four methods in the matching `provider_*.py`;
3. classify its errors; confirm its idempotency behaviour;
4. record the field mapping in `docs/providers/<rail>-<provider>.md`;
5. set `<RAIL>_PROVIDER=<name>` plus credentials and restart the gateway.

If that is genuinely all it takes — no Odoo change, no migration, no touch to
products, purchasing, stock or tills — the architecture has paid for itself.
If it is not, we learn exactly where the abstraction is wrong while only one
provider depends on it.

Do this **before** adding a third shop, a storefront, or analytic accounting.
