# ADR-002: Use Odoo adapters plus a separate Integration Gateway

**Status:** accepted · **Date:** 2026-03

## Context

Mati needs Ethiopian fiscal registration, Ethiopian payment rails and local
delivery couriers. The obvious approach is an Odoo module per provider that
calls the provider's API directly.

That approach puts provider credentials in the ERP database, ties provider
churn to ERP deploys, forces every provider to reimplement retries and
idempotency, and makes the work unusable outside Odoo.

Providers in this market change. A retailer who signs with ArifPay this year may
move to Telebirr next year, and neither of those decisions should require an ERP
migration.

## Decision

Two complementary extension mechanisms with a hard boundary between them.

**Odoo addons** own Odoo-specific concerns: extending models, hooking
lifecycles, mapping Odoo records to a normalized contract, displaying
integration state, consuming responses.

**The Integration Gateway** owns external-system concerns: provider adapters,
HTTP, credentials, authentication, signing, normalization, retries,
idempotency, webhook validation, rate limiting, the integration audit trail and
provider switching.

They communicate over explicit HTTP contracts. The gateway never touches the
Odoo database.

## Consequences

**Good:** swapping a payment provider is an environment variable and a restart.
Credentials never enter the ERP. The gateway can serve a different ERP later
without rewriting the provider work. Provider logic is testable without Odoo —
110 gateway tests run in 16 seconds with no Odoo and no Postgres.

**Bad:** one more service to run, and a network hop that can fail. The second
part is mitigated by design: gateway unavailability is a normal, retryable
state, not an error (ADR-007).

**Rejected alternative — everything in Odoo addons:** simpler to start, and the
reason so many ERP integrations are unmaintainable three years in.

**Rejected alternative — a message queue between them:** the interaction is
request/response with a needed answer. A queue would add infrastructure without
removing a failure mode. Explicitly an anti-goal for this project.
