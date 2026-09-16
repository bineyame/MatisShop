# Architecture

> **Demo fiscal registration — not production certification.** Every provider
> bundled with this platform is a mock. Nothing it produces is a legally valid
> Ethiopian fiscal registration.

## The one-sentence version

Odoo 18 Community does the retail business — products, stock, purchasing,
pricing, POS, sales — and a separate Integration Gateway does everything that
crosses the company boundary: fiscal registration, payment rails, delivery
couriers.

## Why two extension mechanisms

It is tempting to put everything in Odoo addons. That produces a system where
swapping a payment provider means an Odoo migration, where provider
credentials live in the ERP database, and where a fiscal API change forces a
redeploy of the whole ERP.

The split we use instead:

| | Odoo addons | Integration Gateway |
|---|---|---|
| Knows about | Odoo models, Odoo lifecycles, Odoo UI | providers, HTTP, credentials, retries |
| Owns | when a fiscal document is needed, and what it contains | how it reaches a provider and what came back |
| Changes when | Odoo changes, or the business changes | a provider changes |
| Contains secrets | no | yes |
| Can be reused by another ERP | no | yes |

They are not redundant layers. The addon is **the Odoo adapter**; the gateway
is **the external-system boundary**.

```mermaid
flowchart TB
    subgraph ODOO["ODOO 18 COMMUNITY — vanilla"]
        PUR[Purchasing]
        INV[Inventory]
        SAL[Sales]
        POS[Point of Sale]
        WEB[Website / eCommerce]
        TRF[Transfers]
        INV --- POS
        INV --- WEB
        INV --- TRF
    end

    subgraph ADAPT["ODOO ADAPTERS — custom, Odoo-specific"]
        FISC["et_fiscal_odoo"]
        PAY["external_payment_gateway"]
        DEL["external_delivery_gateway"]
    end

    subgraph GW["INTEGRATION GATEWAY — custom, provider-specific"]
        API[Normalized REST contracts]
        IDEM[Idempotency + retries]
        AUD[Integration audit trail]
        REG[Provider registry]
    end

    subgraph PROV["PROVIDERS"]
        FP["Fiscal: mock / MoR / accredited"]
        PP["Payment: mock / ArifPay / Chapa / Telebirr"]
        DP["Delivery: mock / KLIK"]
    end

    PUR --> INV
    POS --> FISC
    SAL --> FISC
    SAL --> PAY
    WEB --> PAY
    TRF --> DEL
    SAL --> DEL

    FISC -->|HTTP, stable contract| API
    PAY -->|HTTP, stable contract| API
    DEL -->|HTTP, stable contract| API

    API --> IDEM --> REG
    IDEM --> AUD
    REG --> FP
    REG --> PP
    REG --> DP
```

## The business workflow

```mermaid
flowchart LR
    S[Supplier<br/>ABC Footwear] --> PO[Purchase Order]
    PO -->|confirm: no stock yet| RCP[Incoming Shipment]
    RCP -->|validate: stock moves| MAIN[(Main Warehouse)]
    MAIN --> TR[Internal Transfer]
    TR --> SHOP1[(Shop 1 Retail)]
    SHOP1 --> SALE[POS Sale<br/>retail pricelist]
    SALE --> PAYMENT[Payment]
    PAYMENT --> DEC[Stock decrement]
    DEC --> FT[et.fiscal.transaction]
    FT --> GWY[Integration Gateway]
    GWY --> MOCK[Mock fiscal provider]
    MOCK --> RES[IRN + QR]
    RES --> ODOO[Stored and displayed in Odoo]
    SHOP1 -.-> DELIV[Delivery where applicable]
```

Every arrow before `et.fiscal.transaction` is **vanilla Odoo**. Everything from
there on is the custom integration boundary.

## Fiscalization is a compliance boundary

Odoo configuration changes often: new products, new shops, new pricelists, an
Odoo upgrade. Fiscal rules change on a completely different clock, and when
they do, the change is legally significant.

```mermaid
flowchart TB
    subgraph FAST["Changes often"]
        A[Odoo configuration]
        B[Products, pricing, shops]
        C[Odoo version upgrades]
    end

    subgraph BOUNDARY["=== COMPLIANCE BOUNDARY ==="]
        D["et_fiscal_odoo<br/>normalized contract, stable"]
    end

    subgraph SLOW["Changes rarely, and legally"]
        E[Integration Gateway fiscal rail]
        F[Certified / approved provider]
    end

    A --> D
    B --> D
    C --> D
    D --> E --> F
```

The contract in `app/domain/contracts.py` is the seam. As long as Odoo can
produce that document, the fiscal side can be replaced without touching Odoo,
and Odoo can be upgraded without touching the fiscal side.

## Fiscal state machine

Two state machines exist deliberately: one in Odoo (what the business record
knows) and one in the gateway (what the integration knows). They are not the
same machine, because Odoo must keep working when the gateway cannot be
reached.

```mermaid
stateDiagram-v2
    [*] --> draft: POS order paid / invoice posted
    draft --> pending: queued
    pending --> submitting: submit
    submitting --> registered: provider returned an IRN
    submitting --> failed: provider or gateway unavailable
    failed --> submitting: retry (same idempotency key)
    registered --> cancelled: cancellation at the provider
    pending --> cancelled
    failed --> cancelled
    registered --> [*]
    cancelled --> [*]
```

Invalid transitions raise. `registered` can never go back to `pending`, so an
IRN cannot be lost by a careless write.

## Idempotency: why exactly one registration

Three independent layers have to agree before a duplicate becomes impossible:

```mermaid
sequenceDiagram
    participant POS as Odoo POS
    participant FT as et.fiscal.transaction
    participant GW as Gateway
    participant DB as Gateway DB
    participant P as Fiscal provider

    POS->>FT: order paid
    FT->>FT: generate idempotency_key (once, forever)
    FT->>GW: POST /fiscal/documents {idempotency_key}
    GW->>DB: INSERT idempotency_records (unique scope+key)
    Note over DB: a duplicate loses on the UNIQUE constraint,<br/>not on an if-statement
    GW->>P: register(document)
    P->>P: look up its own ledger by idempotency_key
    Note over P: a provider that already registered this key<br/>returns the ORIGINAL IRN
    P-->>GW: IRN + QR
    GW->>DB: fiscal_documents (unique idempotency_key)
    GW-->>FT: registered, IRN
    FT->>FT: state = registered
```

1. **Odoo** — `(source_model, source_record_id, document_type)` is unique, so
   one POS order can only ever have one fiscal transaction. The idempotency
   key is generated once at creation and writing a different one raises.
2. **Gateway** — `idempotency_records(scope, idempotency_key)` and
   `fiscal_documents(idempotency_key)` are unique constraints.
3. **Provider** — the mock keeps its own ledger keyed by idempotency key. This
   is the layer that matters when the gateway crashes *after* the provider
   registered but *before* the response was stored. On retry, the provider
   returns the original IRN.

Layer 3 is the one people forget, and it is the one that saves you.

## Offline and failure semantics

A shop with an intermittent connection must keep selling.

```mermaid
sequenceDiagram
    participant C as Cashier
    participant POS as Odoo POS
    participant ST as Stock
    participant FT as Fiscal transaction
    participant GW as Gateway

    C->>POS: sell SAM-BLK-42
    POS->>ST: stock move (shop 1: 8 -> 7)
    Note over ST: the sale is complete and correct
    POS->>FT: create (pending)
    FT->>GW: submit
    GW--xFT: unreachable
    FT->>FT: state = failed, retryable
    Note over FT: the cashier is not blocked
    loop every 5 minutes (ir.cron)
        FT->>GW: retry, same idempotency key
    end
    GW-->>FT: registered, IRN
    FT->>FT: state = registered
```

A provider failure is **not** an HTTP error from the gateway. The gateway
accepted and durably recorded the request, so it answers `200` with
`status: "failed"`. Odoo reads the domain status and keeps its own record
retryable. This is what makes the offline path work; see
`docs/integration-contracts.md`.

> This demonstrates the *architecture* required for offline resilience. It is
> not an implementation of Ethiopia's production offline protocol, which we do
> not have an authoritative specification for.

## Payment provider architecture

```mermaid
flowchart TB
    OD["Odoo payment.transaction<br/>(native framework)"]
    EX["external_payment_gateway<br/>maps to the normalized contract"]
    GW["Gateway /api/v1/payments<br/>idempotency + audit"]
    REG[Provider registry]
    M["MockPaymentProvider<br/>implemented"]
    A["ArifPayProvider<br/>extension point"]
    C["ChapaProvider<br/>extension point"]
    T["TelebirrProvider<br/>extension point"]

    OD --> EX --> GW --> REG
    REG --> M
    REG -.not implemented.-> A
    REG -.not implemented.-> C
    REG -.not implemented.-> T
```

Switching rails is `PAYMENT_PROVIDER=arifpay` plus a restart. No Odoo change,
no database migration, no redeploy of the ERP.

## Deployment topology

```mermaid
flowchart TB
    subgraph HOST["Developer machine — docker compose"]
        subgraph PG["postgres:16"]
            DB1[(odoo<br/>business data)]
            DB2[(integration_gateway<br/>integration data)]
        end
        OD["odoo<br/>:8069<br/>pinned 18.0 source"]
        GW["gateway<br/>:8000<br/>FastAPI"]
        OD --> DB1
        GW --> DB2
        OD -->|HTTP + X-API-Key| GW
    end
    BROWSER[Browser] --> OD
    BROWSER --> GW
```

Note what is **not** drawn: there is no arrow from the gateway to the Odoo
database, and none from Odoo to the gateway database. That is enforced by
configuration — the gateway is only given `GATEWAY_DATABASE_URL` — and it is
the property that lets the gateway serve a different ERP later (ADR-006).

## Productization

```mermaid
flowchart TB
    subgraph SHARED["Shared platform"]
        RF[Odoo retail foundation]
        FC[et_fiscal_odoo]
        PR[external_payment_gateway]
        DR[external_delivery_gateway]
        GWC[Integration Gateway]
    end

    subgraph MATI["Deployment: Mati"]
        M1[mati_demo config]
        M2[(own database)]
    end
    subgraph RB["Deployment: Retailer B"]
        B1[retailer_b config]
        B2[(own database)]
    end

    SHARED --> MATI
    SHARED --> RB
```

Each customer gets their own deployment and their own database. Shared-database
multitenancy is deliberately *not* built (ADR-010): it is the hardest thing to
retrofit out of, and nothing about a three-shop shoe retailer requires it.

## Production hardening

This is a **development** environment. Before anything resembling production:

| Area | Here | Production needs |
|---|---|---|
| Gateway auth | shared `X-API-Key` | mTLS or OAuth2 client credentials, per-client keys, rotation |
| Webhook auth | HMAC over the body with one shared secret | per-provider schemes, timestamp/nonce replay protection |
| Secrets | `.env` file | a secret manager; never on disk in plaintext |
| Transport | plain HTTP inside a Docker network | TLS everywhere, including internal hops |
| Database | one superuser role for both databases | separate least-privilege roles per database |
| Postgres port | bound to `127.0.0.1` | not exposed at all |
| Odoo workers | `workers = 0` (single process) | multi-worker behind a reverse proxy, `proxy_mode = True` |
| `list_db` | `True` | `False`, with a strong master password |
| Fiscal provider | mock | accredited provider, real credentials, digital certificate |
| Rate limiting | none | per-client limits on the gateway |
| Backups | none | PITR on both databases; the fiscal audit trail is a legal record |
| Monitoring | structured logs to stdout | log aggregation, alerting on `fiscal.failed` and retry-backlog depth |

## Further reading

- `docs/vanilla-vs-custom.md` — what is Odoo, what we wrote, and why
- `docs/fiscal-integration.md` — the fiscal rail in detail
- `docs/integration-contracts.md` — the wire contracts and how to add a provider
- `docs/demo-script.md` — the business walkthrough
- `docs/decisions/` — the architecture decision records
