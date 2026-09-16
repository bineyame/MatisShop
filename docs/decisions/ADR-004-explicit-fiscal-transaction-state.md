# ADR-004: Use an explicit fiscal transaction with an explicit state machine

**Status:** accepted · **Date:** 2026-03

## Context

The cheap approach is a few fields on `pos.order`: `fiscal_status`, `irn`,
`qr`. It works until the first outage.

Fiscalization has a lifecycle that does not match the sale's. A paid, completed,
correctly-stocked order whose fiscal registration is still pending is a normal
state, not an error. That registration has to be retried independently, and its
failure history has to survive for audit.

## Decision

A dedicated model, `et.fiscal.transaction`, with:

- an explicit state machine (`draft → pending → submitting → registered`, with
  `failed → submitting` for retry) enforced by `_set_state()`, which refuses
  undeclared transitions;
- an immutable `idempotency_key` generated once at creation;
- full provenance (`source_model`, `source_record_id`);
- the exact request sent and response received;
- `attempt_count` and `last_error`;
- chatter, so every transition is logged against the record.

`UNIQUE (source_model, source_record_id, document_type)` makes duplicate
registration for one sale impossible at the database level.

## Consequences

**Good:** `registered → pending` is impossible, so an IRN cannot be lost to a
careless write. Recovery is a normal operation rather than a manual database
fix. The same model serves POS orders, invoices and anything added later via
`et.fiscal.document.mixin`.

**Bad:** one more model, and a second state machine alongside the gateway's.
That duplication is deliberate: Odoo must keep working when the gateway is
unreachable, so it needs its own view of the world.

**Consequence for the UI:** because registration finishes after the sale, the
receipt printed at payment time cannot carry an IRN. Hence a reprintable fiscal
receipt report rather than a patched POS receipt.
