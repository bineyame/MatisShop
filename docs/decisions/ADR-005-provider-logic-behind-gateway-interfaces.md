# ADR-005: Provider-specific logic belongs behind gateway interfaces

**Status:** accepted · **Date:** 2026-03

## Context

Provider SDKs leak. One provider calls it `merchant_ref`, another `orderId`, a
third nests it under `transaction.meta.reference`. Left unchecked, those names
spread into ERP fields, report templates and reconciliation queries, and
switching provider becomes a migration.

## Decision

Every rail has one abstract interface — `FiscalProvider`, `PaymentProvider`,
`DeliveryProvider` — expressed purely in normalized domain types. No provider's
schema appears in a signature, and the domain contracts
(`app/domain/contracts.py`) contain no provider-specific field.

Provider selection is configuration: `FISCAL_PROVIDER`, `PAYMENT_PROVIDER`,
`DELIVERY_PROVIDER`, resolved through `app/providers/registry.py`.

Adding a provider is one class plus one registry entry.

## Consequences

**Good:** switching provider is an environment variable and a restart. Odoo
cannot tell the difference, so it cannot grow a dependency on one. Provider
adapters are unit-testable in isolation.

**Bad:** a provider with a genuinely richer capability cannot expose it without
a contract change. Accepted — the contract should grow deliberately, not by
accident.

**Enforcement:** `gateway/tests/test_providers.py` asserts that every bundled
and placeholder provider implements its abstract contract with async methods,
and that no placeholder silently pretends to work.
