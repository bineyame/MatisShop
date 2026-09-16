# ADR-009: Use pricelists instead of duplicating products

**Status:** accepted · **Date:** 2026-03

## Context

Mati sells the same shoe at four prices: retail 6,000; online 6,200; wholesale
5,300; wholesale by the case (20+) 5,000.

The naive implementation is four products, or a "wholesale" copy of the
catalogue. It is also how inventory becomes fiction: stock splits across
duplicates, a transfer moves the wrong one, and the reconciliation never
finishes.

## Decision

Native `product.pricelist` and `product.pricelist.item`. One product identity
(ADR-008); price is resolved from the selling context:

| Context | Mechanism |
|---|---|
| Shop 1 till | `pos.config.pricelist_id` = Retail |
| Shop 2 till | `pos.config.pricelist_id` = Wholesale |
| Website | website pricelist = Online |
| Wholesale by the case | the same wholesale list, `min_quantity = 20` |

Quantity breaks are `product.pricelist.item.min_quantity` — vanilla Odoo, no
custom pricing code.

## Consequences

**Good:** one product, one stock number, four prices. Adding a fifth price — a
seasonal promotion, a key account — is a configuration record, not a data
migration. Odoo's own price resolution handles precedence and date ranges.

**Bad:** genuinely complex pricing (compounding promotions, customer-specific
contract grids) will eventually exceed what pricelists express cleanly. When
that happens the answer is still not duplicate products; it is `sale.order`
discounts or a rules module, decided on evidence.

**Verified:** `tools/verify_demo.py` asserts that all four prices resolve from
the same variant, and that exactly one product record carries the SKU.
