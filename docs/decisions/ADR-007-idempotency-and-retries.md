# ADR-007: Use idempotency and retries everywhere that crosses the boundary

**Status:** accepted · **Date:** 2026-03

## Context

Every external call can fail in a way that leaves the outcome unknown: a timeout
after the provider committed, a crash between the provider's response and our
commit, a proxy that retries silently.

For fiscal registration, guessing wrong means declaring revenue twice to a tax
authority. For payments, it means charging a customer twice. Neither is
recoverable by an apology.

Ethiopian connectivity makes this a routine case, not an edge case.

## Decision

Every mutating operation carries a caller-generated idempotency key that is
stable for the lifetime of the logical operation.

Three enforcement layers:

1. **ERP** — `UNIQUE (source_model, source_record_id, document_type)` on the
   fiscal transaction. The key is generated once at creation, and writing a
   different one raises.
2. **Gateway** — `UNIQUE (scope, idempotency_key)` on idempotency records and
   `UNIQUE (idempotency_key)` on each rail resource. Concurrent duplicates race
   on the database, not on an if-statement.
3. **Provider** — the provider deduplicates on the same key. This is the layer
   that covers "registered successfully, response lost".

Retries are bounded and classified: transient errors retry with exponential
backoff inside the gateway; permanent errors do not retry at all. A failed
idempotency claim is released as `failed`, not `completed`, so the same key can
be retried later and still reach exactly one registration.

Failure history is append-only. A later success never overwrites an earlier
failure.

## Consequences

**Good:** the property that matters is testable and tested — same key in, same
IRN out, exactly one registration.
`test_lost_response_cannot_create_a_second_irn` deletes the gateway's entire
memory of a registration and proves the provider layer still prevents a
duplicate.

**Bad:** more moving parts, and idempotency records accumulate. A retention
policy is production work, listed in the implementation report.

**Obligation when integrating a real provider:** confirm that it deduplicates.
If it does not, say so loudly — no amount of gateway code fully compensates.
