# Architecture Decision Records

Short records of the decisions that shaped this platform: the context, what was
decided, and what it costs. A decision without a stated cost is usually a
decision nobody examined.

| # | Decision | One-line reason |
|---|---|---|
| [001](ADR-001-do-not-modify-odoo-core.md) | Do not modify Odoo core | upgrading should be reading a changelog, not a merge |
| [002](ADR-002-odoo-adapters-plus-integration-gateway.md) | Odoo adapters plus a separate Integration Gateway | provider churn must not be ERP churn |
| [003](ADR-003-keep-product-pricing-inventory-native.md) | Keep product, pricing and inventory native | a second stock model is a reconciliation problem |
| [004](ADR-004-explicit-fiscal-transaction-state.md) | Explicit fiscal transaction and state machine | fiscalization has its own lifecycle and must survive outages |
| [005](ADR-005-provider-logic-behind-gateway-interfaces.md) | Provider logic behind gateway interfaces | one provider's schema must not leak into the ERP |
| [006](ADR-006-isolate-odoo-and-gateway-persistence.md) | Isolate Odoo and gateway persistence | each side must be upgradable and restorable alone |
| [007](ADR-007-idempotency-and-retries.md) | Idempotency and bounded retries | registering a sale twice with a tax authority is unrecoverable |
| [008](ADR-008-one-product-identity.md) | One product identity everywhere | otherwise "how many do we have?" has no answer |
| [009](ADR-009-pricelists-not-duplicate-products.md) | Pricelists, not duplicate products | four prices must not mean four stock numbers |
| [010](ADR-010-deployment-isolation-not-multitenancy.md) | Deployment isolation, not shared-database multitenancy | retrofitting multitenancy *out* is harder than in |
| [011](ADR-011-odoo-source-pinning.md) | Pin Odoo to an explicit revision, built from source | "Odoo 18" is a moving branch, not a version |

## Format

Each record states **Context** (the forces, including the tempting wrong
answer), **Decision** (what we actually do), and **Consequences** (good, bad,
and how the decision is enforced or verified). Where a decision is reversible,
the record says when to revisit it.
