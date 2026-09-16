# ADR-010: Separate customer deployments, not shared-database multitenancy

**Status:** accepted · **Date:** 2026-03

## Context

This is the first customer implementation of something intended to serve several
Ethiopian retailers. The instinct at this point is to build multitenancy in from
the start — one database, a `tenant_id` on everything — because "retrofitting it
later is hard".

Retrofitting multitenancy *in* is hard. Retrofitting it *out*, after discovering
that one customer's bad query locks another customer's tills, is harder.

Odoo also fights this: `dbfilter`, the filestore, scheduled jobs and the
database manager all assume a database per deployment. Shared-database
multitenancy in Odoo means working against the framework everywhere.

## Decision

Each customer gets their own deployment: their own Odoo database, their own
gateway database, their own configuration module.

What is shared is **code**, not data:

```
shared platform
├── Odoo retail foundation (vanilla Odoo, configured)
├── et_fiscal_odoo               reusable
├── external_payment_gateway     reusable
├── external_delivery_gateway    reusable
├── Integration Gateway          reusable
└── <client>_demo / <client>_config   per customer
```

Mati installs `mati_demo`. Retailer B installs `retailer_b_config`. Neither
module contains fiscal, payment or delivery logic — that is what makes them
small.

## Consequences

**Good:** a customer's data cannot leak into another's, because it is not in the
same database. One customer can be upgraded, restored or rolled back
independently. A noisy customer cannot degrade another. Odoo works the way Odoo
expects to work. Per-customer backup and retention is trivial, which matters for
fiscal records.

**Bad:** more instances to operate, and per-customer cost is higher at small
scale. Accepted: at the scale of "a handful of Ethiopian retailers", operational
simplicity beats density, and the gateway is small enough that one instance per
customer is cheap.

**Not decided here:** whether a *single* gateway instance could later serve
several customers behind per-customer API keys. The contracts already carry a
`source.system` field and the audit trail is per-resource, so that option stays
open. It is not needed now and is not built.

**Revisit when:** the number of deployments makes upgrades painful — realistically
past a few dozen. At that point the answer is probably deployment automation,
not a shared database.
