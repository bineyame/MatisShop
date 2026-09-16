# ADR-008: One product identity across purchasing, inventory and sales

**Status:** accepted · **Date:** 2026-03

## Context

A shoe retailer buys "Adidas Samba, black, 42" from a factory, stores it in
three locations, sells it over a counter, wholesales it by the case and lists it
online. It is tempting to give each channel its own product record, because each
channel wants different attributes.

The moment that happens, "how many black 42s do we have?" stops having an
answer.

## Decision

One `product.product` variant is the single identity everywhere: purchase
orders, vendor prices, stock quants, transfers, POS, sales orders, invoices,
website.

- `product.template` is the model ("Adidas Samba").
- `product.product` is the thing you can physically hold ("Adidas Samba, black,
  42").
- The SKU (`default_code`) is how the business names it.
- The barcode is what a scanner reads.
- Both resolve to the same variant.

Channel differences are expressed as *context*, not as *records*: pricelists for
price, locations for stock, POS configuration for till behaviour.

## Consequences

**Good:** stock is true by construction. A sale in Shop 1 and a purchase into
the main warehouse move the same identity. Barcode scanning resolves to one
unambiguous variant — `tools/verify_demo.py` asserts that the barcode matches
exactly one product *and* that it is the right one.

**Bad:** a channel-specific attribute has to be modelled as a field or a
pricelist rule rather than as a separate product. That constraint is the point.

**Test:** if someone proposes a second product record for the same physical
shoe, the answer is no. The requirement behind it is a pricing, location or
field question.
