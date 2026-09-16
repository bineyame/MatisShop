# Implementation Report

Mati Retail Platform — Odoo 18 Community + Integration Gateway.

> **Read this section first.** The build machine for this implementation had
> **no Docker installed**. The gateway, its tests and the contract tests were
> executed for real; **Odoo was never started**, so the Odoo addons, the
> seeding and `make verify` are written but unexecuted. Exactly what was and
> was not run is in [Verification status](#verification-status), and the
> specific risk areas are in [Known limitations](#known-limitations).

---

## What works

### Verified by execution

| | Evidence |
|---|---|
| Integration Gateway: fiscal, payment and delivery rails | 110 tests pass in ~16s |
| Idempotency — same key in, same IRN out, one registration | `test_same_idempotency_key_returns_same_registration` |
| A lost response cannot create a second IRN | `test_lost_response_cannot_create_a_second_irn` — deletes the gateway's entire memory of a registration, re-posts, gets the original IRN |
| Provider failure leaves a retryable state | `test_provider_failure_leaves_a_retryable_failed_document` |
| Failure → retry → exactly one registration | `test_failed_document_retries_to_exactly_one_registration` |
| Permanent errors are not retried | `test_provider_rejection_is_a_permanent_failure` |
| Failure history is never overwritten | `test_every_attempt_and_transition_is_audited` |
| Full forensic audit trail | `test_audit_endpoint_answers_the_forensic_questions` |
| Webhook signature verification, rejection and dedup | 8 tests in `test_security.py` |
| Secret redaction in logs and audit rows | `test_audit_payloads_are_redacted` |
| API-key authentication on every `/api/v1` route | `test_api_requires_an_api_key` |
| Payment idempotency, refunds, partial refunds | 12 tests in `test_payments.py` |
| Delivery lifecycle, idempotency, cancellation | 9 tests in `test_delivery.py` |
| Every provider satisfies its abstract contract | `test_providers.py` |
| Placeholders fail loudly instead of pretending | `test_selecting_a_placeholder_provider_surfaces_a_clear_error` |
| Alembic migrations produce the ORM schema | `test_migrations.py` |
| The Odoo payload is exactly what the gateway accepts | `tests/contract/` |
| Demo barcodes are valid, unique EAN-13 | executed against the real generator |

```
110 passed, 1 skipped in 16.49s
```

(The skip is the opt-in live-gateway test, which needs a running stack.)

### Built and statically validated, not executed

The complete Odoo side: four addons, the seeding, the demo controls, the
reports, the Odoo test suites, and the end-to-end verification tool.

Static validation that *was* run:

- every XML file parses;
- every Python file parses;
- every file listed in a manifest exists;
- every internal xmlid reference in our addons resolves;
- every object button in our views maps to a method we define.

---

## What is vanilla Odoo

Roughly 80% of the platform. We configured it; we did not write it.

Products, templates, variants, Color/Size attributes, automatic variant
generation, SKUs, barcodes, barcode→variant resolution, units of measure,
suppliers, vendor price lists (including variant-level), purchase orders, the
confirm-vs-receive distinction, incoming shipments, goods receipts, multi-
warehouse, location hierarchies, stock per location, internal transfers,
inventory adjustments, the stock move audit trail, pricelists, quantity breaks,
POS, POS-to-shop-stock binding, POS contextual pricing, POS stock decrement,
sales orders, invoicing, payment terms, the payment provider framework, the
delivery carrier framework, website/eCommerce, chart of accounts, taxes,
journals.

Row-by-row evidence: [`docs/vanilla-vs-custom.md`](docs/vanilla-vs-custom.md).

## What was configured

Configuration, not development — no business logic was written for any of it:

- company identity: Mati's Shoes PLC, TIN `0012345678`, ETB, Addis Ababa;
- generic chart of accounts and a 15% VAT (Ethiopian VAT rate);
- Odoo feature switches: variants, pricelists, multi-location, multi-warehouse;
- supplier *ABC Footwear Factory* and a wholesale customer;
- Color (Black/White) × Size (41/42) → 8 variants across two shoe models;
- deterministic SKUs and valid EAN-13 barcodes per variant;
- vendor prices, including two variant-specific ones;
- three pricelists plus a 20-unit quantity break;
- three warehouses with deliberately different opening stock;
- opening stock applied through native inventory adjustments;
- two POS configurations, each bound to its own shop stock and pricelist;
- website product publication.

All of it lives in `mati_demo` and is idempotent.

## What required custom Odoo development

Three reusable addons, plus one client module. None of the reusable ones
mentions Mati.

### `et_fiscal_odoo`
Odoo has no concept of Ethiopian fiscal registration. Provides:

- `et.fiscal.transaction` — an explicit fiscal document with its own state
  machine, an immutable idempotency key, full provenance, attempt history and
  the exact request/response;
- `et.fiscal.document.mixin` — implemented by `pos.order` and `account.move`;
  anything else becomes fiscalizable by implementing one method;
- `et.fiscal.gateway.client` — the only Odoo code that speaks HTTP to the
  gateway;
- a retry cron for offline recovery;
- a reprintable fiscal receipt report and an invoice fiscal block, both
  labelled as demo output;
- the company/partner TIN fields.

**Why not vanilla:** the requirement does not exist in Odoo, in any localisation
package, or in any OCA module for Ethiopia.

### `external_payment_gateway`
One `payment.provider` routed through the gateway, built on Odoo's *native*
payment framework rather than a parallel payment system.

**Why not vanilla:** Odoo's framework exists, but no Ethiopian rail is
implemented for it, and we specifically did not want rail credentials in the
ERP.

### `external_delivery_gateway`
One `delivery.carrier` implementing Odoo's standard rate/send/track/cancel
hooks, with courier state on `stock.picking`.

**Why not vanilla:** same reasoning.

### `mati_demo`
Client configuration and seed data only. Deliberately contains no generic
fiscal, payment or delivery code — another retailer installs the three reusable
addons and writes their own equivalent of this one.

## What belongs to the gateway

Everything that crosses the company boundary:

- provider adapters, one module per provider per rail;
- provider credentials — they never enter Odoo;
- authentication and the digital-signing seam;
- request/response normalization;
- **idempotency**, enforced by unique constraints rather than application logic;
- bounded retries with transient/permanent classification;
- webhook signature verification, dedup and rejection logging;
- the integration audit trail: one immutable row per provider attempt, one per
  status transition;
- provider switching by configuration.

**Why here and not in Odoo:** it makes provider churn independent of ERP churn.
Swapping a payment rail is `PAYMENT_PROVIDER=arifpay` and a restart — no Odoo
change, no migration, no downtime for the shops. It is also what makes the work
reusable by a different ERP later, which is the stated productization goal.

The gateway never connects to the Odoo database, and Odoo never connects to the
gateway's. Enforced by configuration: the gateway is only given
`GATEWAY_DATABASE_URL`.

## What is mocked

| Rail | Bundled mock | Behaviour |
|---|---|---|
| Fiscal | `MockFiscalProvider` | own registration ledger keyed by idempotency key; deterministic IRN `ET-DEMO-YYYY-NNNNNN`; base64 QR payload; injectable failure mode |
| Payment | `MockPaymentProvider` | `auto_success` / `manual` / `always_fail`; deterministic provider references |
| Delivery | `MockDeliveryProvider` | seven-state lifecycle advancing on each poll |

**The IRN format, QR payload layout and provider endpoints are invented for
this demo.** They are not derived from any Ethiopian specification.

Real providers are **declared extension points, not implementations**:
`mor`, `accredited`, `arifpay`, `chapa`, `telebirr`, `klik`. Each raises a
structured `ProviderNotConfigured` naming the settings it needs and the
blockers outstanding.

We did not fabricate their APIs. We do not have authoritative current
specifications, and a plausible-looking fake would pass review and fail in
production — strictly worse than an explicit, documented gap.

---

## Verification status

Honest accounting of what was executed on the build machine.

### Executed

| What | Result |
|---|---|
| `gateway/tests` + `tests/contract` (110 tests) | **pass**, run repeatedly |
| Odoo API assumptions checked against the pinned source | **3 real bugs found and fixed** — see below |
| Alembic migration → schema equivalence | **pass** |
| EAN-13 generation for all 8 variants | **pass** — valid and unique |
| Known-good EAN-13 check digits used in tests | **pass** |
| XML / Python / JSON parse across the repo | **pass** |
| Manifest data files exist | **pass** |
| Internal xmlid resolution across addons | **pass** |
| View buttons → defined methods | **pass** |

### Not executed

Docker is not installed on the build machine, so none of this ran:

| What | Why not | Risk |
|---|---|---|
| `make up` / image builds | no Docker | Dockerfiles and compose are unvalidated by execution |
| Odoo startup from the pinned source | no Docker | the source-shadowing approach (ADR-011) is unproven here |
| `make bootstrap` / module installation | no Docker | **highest risk** — see below |
| `mati_demo` seeding | no Docker | field names and API signatures unverified against Odoo 18 |
| Odoo addon test suites | no Docker | written, never run |
| `make verify` end-to-end | no Docker | written, never run |
| POS session/order creation path | no Docker | the least certain code in the repository |

**Anyone re-running this should start with `make up && make bootstrap`, then
`make test-odoo`, then `make verify`.** Every Odoo API used has since been
checked against the pinned source (see below), so the remaining risk is
runtime behaviour rather than wrong method and field names.

### API verification against the pinned Odoo source

Docker was unavailable, but the pinned revision's source is not. Every
Odoo API this platform depends on was checked by reading
`odoo/odoo@d60ab9c` directly. **Three real bugs were found and fixed this
way, before a container was ever started.**

Bugs found and fixed:

| Bug | Detail | Fix |
|---|---|---|
| `pos.order.amount_paid` is a plain stored field, **not** computed from `pos.payment` | `action_pos_order_paid()` raises *"Order … is not fully paid"* unless it is set explicitly. `verify_demo.py` created the order with `amount_paid = 0`, then a payment, then called it — guaranteed failure at demo step 8. | set `amount_paid` explicitly after creating the payment |
| `external_delivery_gateway` had a missing dependency | `carrier_id` and `carrier_tracking_ref` on `stock.picking` come from **`stock_delivery`**, not `delivery` or `stock`. The module would have failed to install. | added `stock_delivery` to `depends` |
| refund set state on the wrong record | `_send_refund_request()` returns a **child** transaction; the refund's state belongs on that child, not on the source transaction which is already `done`. The signature was also `**kwargs` instead of `amount_to_refund=None`. | act on `refund_tx`, match the core signature |

Confirmed correct (no change needed):

| Assumption | Verified |
|---|---|
| `action_pos_order_paid`, `_create_order_picking`, `sync_from_ui`, `_should_create_picking_real_time` | all exist on `pos.order` |
| POS decrements stock immediately | `point_of_sale_update_stock_quantities` defaults to `'real'`, so `update_stock_at_closing` is False |
| `_create_order_picking` uses `config_id.picking_type_id` | confirmed — this is what binds a sale to *that shop's* stock |
| `try_loading(template_code, company, install_demo=False, ...)` | signature matches exactly |
| storable products are `type='consu'` + `is_storable=True` | `is_storable` is contributed by the `stock` module, which we depend on |
| `pos.order.line` fields (`qty`, `price_unit`, `price_subtotal`, `price_subtotal_incl`, `full_product_name`, `tax_ids`, `tax_ids_after_fiscal_position`) | all exist |
| `pos.config` fields (`picking_type_id`, `use_pricelist`, `pricelist_id`, `available_pricelist_ids`, `payment_method_ids`) | all exist |
| `pos.session.action_pos_session_open` / `_closing_control` | exist; our `*args` override is signature-compatible |
| `stock.quant._get_available_quantity(product, location, …)` | positional call is correct |
| `product.pricelist._get_product_price(product, *args)` | quantity-as-positional is correct |
| `account.move.pos_order_ids` | exists — the double-fiscalization guard works |
| `TestPoSCommon` helpers used by the POS test (`create_product`, `open_new_session`, `create_ui_order_data`, `basic_config`, `categ_basic`) | all exist; `create_product` is a classmethod with our exact signature |
| `sync_from_ui` returns a dict keyed by model | confirmed — it returns `read_pos_data()`, so `results["pos.order"][0]["id"]` is correct |
| payment hooks (`_send_payment_request`, `_get_specific_rendering_values`, `_get_tx_from_notification_data`, `_process_notification_data`, `_set_*`) | all exist |
| `delivery.carrier` dispatch is `getattr(self, '%s_rate_shipment' % delivery_type)` | our `integration_gateway_*` naming is correct |
| every inherited view xmlid | `view_pos_pos_form`, `view_delivery_carrier_form`, `payment_provider_form`, `payment_transaction_form`, `view_picking_form`, `view_move_form`, `report_invoice_document`, `res_config_settings_view_form` — all present |
| every xpath anchor | `//sheet`, `//notebook`, `name="provider_credentials"` — all present |
| settings `<app>`/`<block>`/`<setting>` markup | matches how `point_of_sale` writes it (`data-string` added to match) |
| `/web/health` route | exists — the compose healthcheck is valid |
| `quote_plus` in the QWeb context, `/report/barcode/?barcode_type=QR&…` | both exist; the QR image markup matches Odoo's own usage |

### What could still need fixing

Reading source proves signatures and field names; it does not prove runtime
behaviour. Remaining risk, highest first:

1. **`pos.order` creation as a whole.** Every individual field and method is
   confirmed, but constructing a paid order outside the POS front end
   exercises constraints and computes that only run at runtime.
2. **Chart-of-accounts application.** The signature is right; whether
   `generic_coa` yields a usable cash journal for POS is a runtime question.
4. **Module install ordering and demo seeding end to end**, including the ETB
   currency switch and the warehouse rename.
5. **Docker builds themselves** — never executed.

None of these are architectural.

---

## Mandatory completion checklist

`[x]` executed and passing · `[~]` implemented, not executed here · `[ ]` not done

**Environment**
```
[x] Odoo 18 Community source pinned      d60ab9c928f0ea3d31ef11fc54bf3b9549b082e8
[~] PostgreSQL starts                    compose + healthcheck written
[~] Odoo starts                          Dockerfile + entrypoint written
[~] Gateway starts                       runs locally under uvicorn; image not built
[~] health checks pass                   endpoints implemented and unit-tested
[~] data persists across restart         named volumes configured
```

**Product**
```
[~] product template exists              seeded by mati_demo
[~] Color attribute exists
[~] Size attribute exists
[~] variants generated                   4 per template, create_variant=always
[~] SKUs assigned                        deterministic
[x] barcodes assigned                    generator executed: 8 valid, unique EAN-13
```

**Purchasing**
```
[~] supplier exists
[~] vendor price exists                  incl. variant-level 3,200 / 3,100
[~] PO can be created
[~] PO can be confirmed
[~] receipt is created
[~] confirming PO does not fake receipt  asserted by verify_demo.py
[~] validating receipt increases stock   asserted by verify_demo.py (10 -> 30)
```

**Inventory**
```
[~] Main Warehouse exists
[~] Shop 1 exists
[~] Shop 2 exists
[~] stock differs by location
[~] internal transfer works
[~] source decreases                     asserted (30 -> 25)
[~] destination increases                asserted (3 -> 8)
```

**Pricing**
```
[~] retail pricelist                     6,000
[~] online pricelist                     6,200
[~] wholesale pricelist                  5,300
[~] bulk quantity rule                   5,000 at 20+
[~] same product produces different prices
```

**POS**
```
[~] Retail POS configured
[~] Wholesale POS configured
[~] barcode/product resolution works     asserted by verify_demo.py
[~] correct price applies
[~] sale completes
[~] correct shop stock decreases         asserted (8 -> 7), main untouched
```

**Fiscal**
```
[~] fiscal transaction generated
[~] gateway request generated
[x] mock fiscal provider called
[x] IRN returned
[x] QR returned
[~] result stored in Odoo
[x] idempotency enforced                 3 layers, tested
[x] duplicate submission does not duplicate IRN
[x] provider failure can be simulated
[x] retry works
[x] audit information retained           append-only, tested
```

**Extensibility**
```
[x] payment provider contract exists
[x] mock payment path exists
[x] delivery provider contract exists
[x] mock delivery seam exists
[x] future provider instructions documented
```

**Quality**
```
[x] gateway + contract tests pass         110 passed
[~] Odoo tests pass                       written, not executed
[~] reset procedure works                 written, not executed
[~] clean bootstrap works                 written, not executed
[x] README sufficient for another developer
[x] Mermaid architecture exists           8 diagrams
[x] ADRs exist                            11
[x] vanilla-vs-custom matrix exists
```

---

## What remains for real Ethiopian deployment

**Fiscal — the long pole**
1. Authoritative MoR technical API specification, or a contract with an
   accredited fiscal service provider.
2. Accreditation / certification of this integration.
3. Production credentials and taxpayer enrolment.
4. Digital certificate integration for QR signing (the seam exists in the
   gateway; the certificate does not).
5. The precise offline resiliency specification: buffering limits, permitted
   offline window, sequence requirements. What we built demonstrates the
   *architecture* for offline resilience, not Ethiopia's protocol.
6. **Confirm the real provider deduplicates on an idempotency key.** If it does
   not, escalate — no gateway code fully compensates.
7. The real IRN and QR payload formats (ours are invented).

**Payment**
8. Merchant accounts and live credentials for the chosen rail(s).
9. Current API references and sandbox access.
10. Confirmed webhook signature schemes per provider.
11. Settlement and reconciliation rules.

**Delivery**
12. Courier merchant account, API reference, coverage and pricing rules.

**Platform**
13. Production hardening in full — see
    [`docs/architecture.md#production-hardening`](docs/architecture.md#production-hardening).
14. Backups with PITR on both databases. The fiscal audit trail is a legal
    record.
15. Monitoring and alerting on `fiscal.failed` and retry-backlog depth.
16. A retention policy for idempotency records and audit rows.
17. Real Odoo user accounts, roles and password policy.
18. An Ethiopian chart of accounts if one is required for statutory reporting
    (we use the generic chart).

---

## Known limitations

No hiding them.

1. **The Odoo side has never been run.** The single largest caveat. Every API
   it uses has been verified against the pinned Odoo source, and three real
   bugs were fixed that way, but reading source is not running code. See
   [Verification status](#verification-status).
2. **Fiscalization is not compliant and is not certified.** Mock provider,
   invented IRN and QR formats, no legal validity.
3. **No real payment rail; no funds move. No real courier; nothing ships.**
4. **Security is development grade.** One shared API key, one HMAC secret for
   all webhooks, secrets in a `.env` file, plain HTTP inside the Docker network,
   a single database superuser for both databases.
5. **Single Odoo process** (`workers = 0`), no reverse proxy, no TLS.
6. **The POS receipt printed at sale time carries no IRN,** because registration
   completes after the sale. A reprintable fiscal receipt report is provided
   instead. This is honest rather than ideal — see item 3 of the next section.
7. **Website/eCommerce is configured but shallow.** Products are published and
   the online pricelist exists; no storefront design work was done.
8. **Credit wholesale is vanilla Odoo only.** Sales order → delivery → invoice →
   receivable works, but there is no wholesale-specific UI or credit-limit
   logic.
9. **No gateway rate limiting.** Listed as a gateway responsibility, not
   implemented.
10. **Odoo→gateway calls are synchronous and inline.** A slow gateway adds
    latency to `action_pos_order_paid`. It cannot *block* a sale — failures are
    swallowed and the document stays retryable — but under a hanging connection
    the cashier waits for the HTTP timeout. Moving submission to the cron path
    entirely (`et_fiscal.auto_submit = False`) is a one-setting mitigation that
    is already supported.
11. **The gateway's outbound ERP notifier is minimal.** The reference flow is
    Odoo polling, which is the right default; push is a stub.
12. **No load or concurrency testing.** Idempotency is tested for correctness
    under simulated races, not under real concurrency.
13. **The mock providers live inside the gateway process.** Convenient for a
    demo; a real provider is a network hop with different failure modes.

---

## Recommended next vertical slice

**Boot the stack and make `make verify` pass.**

Not a new feature — the smallest step with the highest value, because it turns
every `[~]` in the checklist above into `[x]` or into a specific bug. Expect to
spend the time on Odoo 18 API details in the POS path, in roughly the order
given in [the specific things most likely to need fixing](#the-specific-things-most-likely-to-need-fixing).

Concretely:

```bash
make up && make bootstrap    # fix install errors as they surface
make test-odoo               # fix test-level API drift
make verify                  # fix the POS creation path, then watch it pass
```

**After that**, in priority order:

1. **POS front-end fiscal indicator.** Show a "fiscal registration pending"
   marker on the POS receipt at sale time, so a cashier knows the state without
   opening the back office. Deliberately deferred because a bad xpath into the
   POS front end breaks the till.
2. **A fiscal operations dashboard.** "How many documents are waiting, how old
   is the oldest, which ones need a human." Everything needed is already in the
   data; it is a view, not a model.
3. **The first real provider.** Once a fiscal provider contract exists, that
   integration is one class in the gateway — and it is the moment the whole
   architecture either pays off or does not.
