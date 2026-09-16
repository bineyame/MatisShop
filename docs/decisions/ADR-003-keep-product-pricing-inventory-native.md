# ADR-003: Keep product, pricing and inventory native to Odoo

**Status:** accepted · **Date:** 2026-03

## Context

Retail ERP projects routinely grow a parallel product model — a "shop item"
table, a "wholesale catalogue", a custom stock table "for performance". Each one
starts as a small convenience and ends as a reconciliation problem.

Odoo 18 Community already has a mature model: templates, variants, attributes,
warehouses, locations, quants, moves, pricelists, quantity breaks.

## Decision

Product, pricing and inventory are vanilla Odoo. We configure them; we do not
reimplement them.

Concretely: no custom product table, no custom stock table, no custom pricing
engine, and no direct writes to quantity fields — stock changes only through
stock moves and inventory adjustments.

## Consequences

**Good:** inventory is always explainable as opening + receipts ± transfers −
sales ± adjustments, because every unit is backed by a `stock.move`.
`tools/verify_demo.py` asserts exactly this invariant. Odoo's reporting,
traceability and barcode tooling work without any help from us.

**Bad:** we accept Odoo's model even where a bespoke one would fit Mati
marginally better. That trade is worth it.

**Test:** if a future requirement seems to need a second product or stock
model, that is a signal the requirement is being modelled wrong — most often it
is a pricing question (ADR-009) or a location question.
