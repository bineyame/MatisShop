# ADR-006: Isolate Odoo and gateway persistence

**Status:** accepted · **Date:** 2026-03

## Context

The gateway needs durable state: idempotency records, provider references,
request and response audit, retry state, webhook events. The shortest path is to
put those tables in the Odoo database, or to let the gateway read Odoo's tables
directly instead of taking data over HTTP.

Both are traps. Sharing a schema means Odoo migrations can break the gateway and
vice versa. Reading Odoo's tables couples the gateway to Odoo's internals —
which kills the "serve another ERP later" property that justified building it
separately (ADR-002).

## Decision

Two logical databases, `odoo` and `integration_gateway`, on one PostgreSQL
server in development.

- Odoo owns its business database. The gateway never connects to it.
- The gateway owns its integration database. Odoo never connects to it.
- They exchange data only over the HTTP contracts.
- Gateway schema changes go through Alembic migrations, independent of Odoo
  module upgrades.

## Consequences

**Good:** either side can be upgraded, restored or replaced independently. The
gateway has no Odoo dependency at all — its tests run with neither Odoo nor
PostgreSQL present. Backup and retention policies can differ, which matters
because the fiscal audit trail is a legal record with a different lifetime from
a POS session.

**Bad:** some data is duplicated — a fiscal document exists on both sides. That
is intentional. They are different records serving different purposes, linked by
the idempotency key, and each stays correct when the other is unavailable.

**Enforcement:** the gateway container is given only `GATEWAY_DATABASE_URL`. It
holds no credentials for the Odoo database.
