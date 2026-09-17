# Implementation Report

Mati Retail Platform — Odoo 18 Community + Integration Gateway.

> **Everything in this report was executed.** The platform was built,
> bootstrapped from a wiped state, seeded, tested and verified end to end
> against real Odoo 18, real PostgreSQL and the real Integration Gateway over
> HTTP. Nothing below is asserted from reading code alone.
>
> The fiscal, payment and delivery **providers remain mocks** - that is a
> deliberate design boundary, not an unfinished edge. See
> [What is mocked](#what-is-mocked) and
> [What is NOT production ready](#known-limitations).

## Results

| Suite | Result |
|---|---|
| Gateway + cross-boundary contract tests | **110 passed**, 1 skipped (laptop and in-image) |
| Odoo addon test suites | **57 passed, 0 failed, 0 errors** |
| End-to-end verification (`make verify`) | **103 checks, 0 failed** |
| Clean rebuild from wiped volumes | **bootstrap exit 0, verify exit 0** |
| Service health | postgres, odoo, gateway all `ok` |

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

### Odoo side — verified by execution

Every claim below was observed in a running Odoo 18 against PostgreSQL.

| | Evidence |
|---|---|
| All four addons install cleanly | `bootstrap exit 0` from wiped volumes |
| Odoo runs the **pinned** source | container logs `revision d60ab9c…`; `import odoo` resolves to `/opt/odoo/source/odoo/__init__.py` |
| Product template with Color × Size → 4 variants | verify step 1 |
| SKU and barcode are distinct identifiers | `SAM-BLK-42` / `2000004200008` |
| Barcode resolves to exactly one, correct variant | verify step 1 |
| All 8 barcodes are valid, unique EAN-13 | check digit recomputed in-test |
| Stock differs per location | `MAIN=10 SHOP1=3 SHOP2=6` |
| **Confirming a PO does NOT move stock** | stayed at 10 |
| **Validating the receipt does** | 10 → 30 |
| Internal transfer moves both sides | main 30 → 25, shop 1 3 → 8 |
| Four prices from one variant | 6000 / 6200 / 5300 / 5000 ETB |
| Exactly one product record per SKU | verify step 7 |
| POS bound to its own shop's stock and pricelist | Shop 1 → SHOP1 + Retail |
| POS sale decrements that shop only | shop 1 8 → 7, main untouched at 25 |
| Fiscal transaction created and linked to the order | `FT/2026/000001` → `pos.order` |
| Registered through the gateway | `IRN ET-DEMO-2026-000003` + QR |
| Duplicate submission returns the same IRN | verify step 12 |
| **Outage: sale completes, stock correct, fiscal `failed` + retryable** | verify step 13 |
| **Recovery: retry → registered, same identity, one registration** | verify step 13 |
| Gateway audit preserves every attempt | `[transient, transient, transient, success]` |
| Inventory reconciles to its stock moves | invariant section |
| `make seed` is idempotent | re-run changes nothing |
| `make reset` restores the opening state | exact quantities restored |
| Compose healthchecks work | `/web/health` 200; all three services `ok` |

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

Everything was executed. The sequence, from a completely wiped state:

```
docker compose down -v          # remove containers AND volumes
docker compose up -d            # postgres + odoo + gateway
bash scripts/bootstrap.sh       # create db, install 4 addons, seed    -> exit 0
bash scripts/verify.sh          # full end-to-end vertical slice       -> exit 0
```

with both suites re-run against that fresh database afterwards.

### What executing it actually found

Building and running the system surfaced **20 real defects** that reading the
code had not. They are worth listing, because the ratio is the point: static
review and a careful reading of the pinned Odoo source caught three; running
the thing caught the rest.

**Found by reading the pinned Odoo source (before Docker existed on the box):**

| Bug | Impact |
|---|---|
| `pos.order.amount_paid` is a plain stored field, not computed from `pos.payment` | `action_pos_order_paid()` would have raised *"Order is not fully paid"* at the POS step |
| `external_delivery_gateway` missing its `stock_delivery` dependency | `carrier_id` / `carrier_tracking_ref` come from that module; install would fail |
| `_send_refund_request` set state on the source transaction | it returns a *child*; the refund's state belongs on the child |

**Found by building the images:**

| Bug | Impact |
|---|---|
| `pip install .` ran before `app/` was copied | gateway image never built |
| `uvicorn --log-config /dev/null` | uvicorn treats it as a fileConfig; container crash-looped |
| CRLF shebang in `docker-entrypoint.sh` | *"no such file or directory"* for a file that exists |
| shallow git clone of Odoo (~400 MB, one connection, unresumable) | died at 570 s on a slow link; replaced with a retryable tarball of the same SHA |

**Found by installing the addons into Odoo:**

| Bug | Impact |
|---|---|
| `ir.cron.numbercall` removed in Odoo 17 | aborted the whole `et_fiscal_odoo` install |
| `//div[@class='page']` in the invoice report | real template is `class="page mb-4"`; exact match cannot locate it |
| one cash payment method shared by two POS configs | Odoo forbids it - each till reconciles its own drawer |
| company currency set *before* loading the chart of accounts | `try_loading()` overwrote ETB with USD from the US fiscal country; every price would have been in dollars |

**Found by running the scripts:**

| Bug | Impact |
|---|---|
| `docker compose exec` bypasses ENTRYPOINT | seed/reset/verify and both `shell-odoo` targets gave Odoo no `--db_host` |
| `odoo-bin` needs its subcommand first | entrypoint emitted `-c odoo.conf … shell`; odoo-bin rejected `shell` |
| `post_init_hook` does not run on `-u` | a re-seed silently did nothing at all |

**Found by running the verification:**

| Bug | Impact |
|---|---|
| `not child_of` is not an Odoo domain operator | `ValueError: Invalid leaf`; the clause was unnecessary anyway |
| `action_pos_session_open()` leaves a session in `opening_control` | `set_opening_control()` is the public API that opens one |
| `pos.config.open_ui()` refuses `SUPERUSER_ID` | correct behaviour - a session belongs to a cashier, not OdooBot |

**Found by running the test suites:**

| Bug | Impact |
|---|---|
| gateway tests used `os.environ.setdefault` | inherited the deployment's API key in-container: 61 failures there, 0 on a laptop |
| the fiscal cron committed per document inside tests | Odoo forbids it; guarded with the predicate Odoo core itself uses |
| re-seeding wrote `pos.config` fields while a session was open | `make seed`, documented as safe to re-run, was not |
| two Odoo tests assumed an empty database | a fixed `source_record_id` collided with a real POS order's fiscal transaction |

Three of these - the non-hermetic gateway tests, the non-idempotent seed, and
the tests assuming a pristine database - are the same underlying mistake:
**code written against an imagined environment rather than the one that
exists.** That pattern is worth watching for in review.

## Mandatory completion checklist

`[x]` executed and passing · `[ ]` not done

Every item was verified on a stack rebuilt from wiped volumes.

**Environment**
```
[x] Odoo 18 Community source pinned      d60ab9c928f0ea3d31ef11fc54bf3b9549b082e8
[x] PostgreSQL starts                    healthy; both databases created
[x] Odoo starts                          healthy, on the pinned revision
[x] Gateway starts                       image built, healthy
[x] health checks pass                   postgres/odoo/gateway all ok
[x] data persists across restart         survived container recreation
```

**Product**
```
[x] product template exists              seeded by mati_demo
[x] Color attribute exists
[x] Size attribute exists
[x] variants generated                   4 per template, create_variant=always
[x] SKUs assigned                        deterministic
[x] barcodes assigned                    8 valid, unique EAN-13, in the database
```

**Purchasing**
```
[x] supplier exists
[x] vendor price exists                  incl. variant-level 3,200 / 3,100
[x] PO can be created
[x] PO can be confirmed
[x] receipt is created
[x] confirming PO does not fake receipt   asserted by verify
[x] validating receipt increases stock   asserted by verify (10 -> 30)
```

**Inventory**
```
[x] Main Warehouse exists
[x] Shop 1 exists
[x] Shop 2 exists
[x] stock differs by location
[x] internal transfer works
[x] source decreases                     asserted (30 -> 25)
[x] destination increases                asserted (3 -> 8)
```

**Pricing**
```
[x] retail pricelist                     6,000
[x] online pricelist                     6,200
[x] wholesale pricelist                  5,300
[x] bulk quantity rule                   5,000 at 20+
[x] same product produces different prices
```

**POS**
```
[x] Retail POS configured
[x] Wholesale POS configured
[x] barcode/product resolution works   asserted by verify
[x] correct price applies
[x] sale completes
[x] correct shop stock decreases         asserted (8 -> 7), main untouched
```

**Fiscal**
```
[x] fiscal transaction generated
[x] gateway request generated
[x] mock fiscal provider called
[x] IRN returned
[x] QR returned
[x] result stored in Odoo
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
[x] gateway + contract tests pass         110 passed, laptop and in-image
[x] Odoo tests pass                       57 passed, 0 failed
[x] reset procedure works                 restores exact opening stock
[x] clean bootstrap works                 from wiped volumes, exit 0
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

1. **Fiscalization is not compliant and is not certified.** Mock provider,
   invented IRN and QR formats, no legal validity.
2. **No real payment rail; no funds move. No real courier; nothing ships.**
3. **Security is development grade.** One shared API key, one HMAC secret for
   all webhooks, secrets in a `.env` file, plain HTTP inside the Docker network,
   a single database superuser for both databases.
4. **Single Odoo process** (`workers = 0`), no reverse proxy, no TLS.
5. **The POS receipt printed at sale time carries no IRN,** because registration
   completes after the sale. A reprintable fiscal receipt report is provided
   instead. This is honest rather than ideal — see the next section.
6. **Website/eCommerce is configured but shallow.** Products are published and
   the online pricelist exists; no storefront design work was done.
7. **Credit wholesale is vanilla Odoo only.** Sales order → delivery → invoice →
   receivable works, but there is no wholesale-specific UI or credit-limit
   logic.
8. **No gateway rate limiting.** Listed as a gateway responsibility, not
   implemented.
9. **Odoo→gateway calls are synchronous and inline.** A slow gateway adds
    latency to `action_pos_order_paid`. It cannot *block* a sale — failures are
    swallowed and the document stays retryable — but under a hanging connection
    the cashier waits for the HTTP timeout. Moving submission to the cron path
    entirely (`et_fiscal.auto_submit = False`) is a one-setting mitigation that
    is already supported.
10. **The gateway's outbound ERP notifier is minimal.** The reference flow is
    Odoo polling, which is the right default; push is a stub.
11. **No load or concurrency testing.** Idempotency is tested for correctness
    under simulated races, not under real concurrency.
12. **The mock providers live inside the gateway process.** Convenient for a
    demo; a real provider is a network hop with different failure modes.

---

## Recommended next vertical slice

The previous recommendation - *boot the stack and make `make verify` pass* - is
done. Every `[~]` in the checklist is now `[x]`.

**The next smallest valuable step is the first real provider.**

Everything else is refinement; this is the step that either validates the
architecture or exposes it. Concretely, once a fiscal provider contract exists:

1. implement one class against `FiscalProvider` in
   `gateway/app/providers/fiscal/`;
2. classify its errors as transient or permanent so the existing retry logic
   keeps working;
3. confirm it deduplicates on an idempotency key - and escalate loudly if it
   does not, because no gateway code fully compensates;
4. set `FISCAL_PROVIDER=<name>` and restart.

If that is genuinely all it takes, the boundary paid for itself. If it is not,
we learn exactly where the abstraction is wrong while it is still cheap.

**After that**, in priority order:

1. **POS front-end fiscal indicator.** Show a "fiscal registration pending"
   marker on the POS receipt at sale time, so a cashier sees the state without
   opening the back office. Deferred because a bad xpath into the POS front end
   breaks the till - a risk this session demonstrated twice with report and
   view xpaths.
2. **A fiscal operations dashboard.** "How many documents are waiting, how old
   is the oldest, which need a human." The data already exists; it is a view,
   not a model.
3. **Concurrency testing of the idempotency guarantees.** They are proven
   correct under simulated races, not under real parallel load.
