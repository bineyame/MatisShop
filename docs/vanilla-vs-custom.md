# Vanilla Odoo vs. custom code

The rule this project follows: **check whether Odoo 18 Community already does
it before writing anything.** For a shoe retailer, the answer is "yes" far more
often than people expect.

Columns:

- **Vanilla Odoo** — the capability exists in Odoo 18 Community out of the box.
- **Configuration** — we configured it (records, settings, seed data), but wrote
  no business logic.
- **Odoo addon** — we wrote Odoo-side code.
- **Gateway** — the Integration Gateway is involved.

## The matrix

| Requirement | Vanilla Odoo | Configuration | Odoo addon | Gateway | Where |
|---|:--:|:--:|:--:|:--:|---|
| **Product model** |
| Product template | Yes | Yes | No | No | `product.template` |
| Product variants | Yes | Yes | No | No | `product.product` |
| Color / Size attributes | Yes | Yes | No | No | `product.attribute` |
| Automatic variant generation | Yes | Yes | No | No | `create_variant = always` |
| Internal reference (SKU) | Yes | Yes | No | No | `product.default_code` |
| Barcode | Yes | Yes | No | No | `product.barcode` |
| Barcode → variant resolution | Yes | Yes | No | No | POS + `stock.barcode` |
| Unit of measure | Yes | Yes | No | No | `uom.uom` |
| **Purchasing** |
| Supplier / vendor records | Yes | Yes | No | No | `res.partner` |
| Vendor price lists | Yes | Yes | No | No | `product.supplierinfo` |
| Variant-level vendor pricing | Yes | Yes | No | No | `supplierinfo.product_id` |
| Purchase order | Yes | Yes | No | No | `purchase.order` |
| PO confirmation ≠ stock increase | Yes | No | No | No | `stock.picking` state machine |
| Incoming shipment / receipt | Yes | Yes | No | No | `stock.picking` |
| Goods receipt increases stock | Yes | No | No | No | `stock.move` / `stock.quant` |
| **Inventory** |
| Multi-shop (warehouse per shop) | Yes | Yes | No | No | `stock.warehouse` |
| Location hierarchy | Yes | Yes | No | No | `stock.location` |
| Stock per location | Yes | Yes | No | No | `stock.quant` |
| Internal transfers | Yes | available, unused | No | No | Mati receives supplier → shop directly |
| Inventory adjustments | Yes | Yes | No | No | `stock.quant` inventory mode |
| Stock move audit trail | Yes | No | No | No | `stock.move.line` |
| Reordering rules | Yes | not used | No | No | `stock.warehouse.orderpoint` |
| **Pricing** |
| Pricelists | Yes | Yes | No | No | `product.pricelist` |
| Multiple pricelists per product | Yes | Yes | No | No | `product.pricelist.item` |
| Quantity break pricing | Yes | available, unused | No | No | `item.min_quantity` — Mati prices per model |
| Per-customer pricing | Yes | available, unused | No | No | `partner.property_product_pricelist` |
| **Selling** |
| Point of sale | Yes | Yes | No | No | `pos.config`, `pos.order` |
| POS bound to a shop's stock | Yes | Yes | No | No | `pos.config.picking_type_id` |
| POS contextual pricing | Yes | Yes | No | No | `pos.config.pricelist_id` |
| POS sale decrements stock | Yes | No | No | No | `pos.order._create_order_picking` |
| Sales orders (credit wholesale) | Yes | Yes | No | No | `sale.order` |
| Customer invoicing | Yes | Yes | No | No | `account.move` |
| Payment terms / receivables | Yes | Yes | No | No | `account.payment.term` |
| Website / eCommerce | Yes | Yes | No | No | `website_sale` |
| One catalog across all channels | Yes | Yes | No | No | shared `product.product` |
| **Taxes and accounting** |
| Chart of accounts | Yes | Yes | No | No | `generic_coa` |
| VAT 15% | Yes | Yes | No | No | `account.tax` |
| Journals | Yes | Yes | No | No | `account.journal` |
| **Fiscalization** |
| Ethiopian fiscal registration | **No** | No | **Yes** | **Yes** | `et_fiscal_odoo` + gateway |
| Fiscal transaction record | **No** | No | **Yes** | No | `et.fiscal.transaction` |
| Fiscal state machine | **No** | No | **Yes** | **Yes** | both sides |
| IRN / QR storage | **No** | No | **Yes** | **Yes** | both sides |
| IRN / QR on receipt + invoice | **No** | No | **Yes** | No | QWeb reports |
| Idempotent registration | **No** | No | **Yes** | **Yes** | unique constraints, 3 layers |
| Retry after provider outage | **No** | No | **Yes** | **Yes** | `ir.cron` + gateway retries |
| Fiscal audit trail | Partial | No | **Yes** | **Yes** | chatter + `integration_requests` |
| Company TIN | **No** | Yes | **Yes** | No | field added to `res.company` |
| Factory / Model as product metadata | **No** | Yes | **Yes** | No | fields added to `product.template` |
| Shop-level sales attribution | Yes | Yes | No | No | `pos.order.config_id` |
| Shop-level stock reporting | Yes | Yes | No | No | `stock.quant` by location |
| Integration status screen | **No** | No | **Yes** | No | `mati.integration.status` |
| Spreadsheet normalization | **No** | No | No | No | `tools/normalize_mati_inventory.py` |
| **Payment rails** |
| Payment provider framework | Yes | Yes | No | No | `payment.provider` |
| Payment transaction lifecycle | Yes | Yes | No | No | `payment.transaction` |
| Ethiopian payment rails | **No** | No | **Yes** | **Yes** | `external_payment_gateway` |
| Provider credentials handling | Partial | No | No | **Yes** | gateway environment |
| Provider retries / idempotency | **No** | No | No | **Yes** | gateway |
| Webhook signature verification | Partial | No | No | **Yes** | gateway |
| Swapping payment provider | **No** | **Yes** | No | **Yes** | `PAYMENT_PROVIDER` env var |
| **Delivery** |
| Delivery carrier framework | Yes | Yes | No | No | `delivery.carrier` |
| Shipping on pickings | Yes | Yes | No | No | `stock.picking` |
| Ethiopian couriers | **No** | No | **Yes** | **Yes** | `external_delivery_gateway` |
| Normalized delivery lifecycle | **No** | No | **Yes** | **Yes** | seven states |
| **Cross-cutting** |
| Reporting / auditability | Yes | Yes | Partial | Partial | Odoo reports + gateway audit |
| Access control | Yes | Yes | Partial | No | groups + record rules |
| Multi-company | Yes | not used | No | No | one company per deployment |

## Reading the matrix

**Roughly 80% of this platform is vanilla Odoo, configured.** Everything the
retailer actually does every day — buy shoes, receive them, move them between
shops, price them differently per channel, sell them, invoice them — is Odoo
doing its job.

The custom code is concentrated in exactly one place: **things that cross the
company boundary.** Fiscal registration, payment rails, courier bookings. Those
are the three things Odoo genuinely cannot know about, because they depend on
Ethiopian systems that Odoo has never heard of.

### Things we deliberately did NOT build

| Temptation | Why not |
|---|---|
| Multi-company for the two shops | They are one legal entity with one TIN. Multi-company would create fake financial separation and break consolidated reporting |
| A central warehouse | Mati has none. Stock goes supplier → shop. Inventing a hub would model a business that does not exist |
| Factory as a product attribute | A shoe has exactly one factory. As an attribute it would multiply every variant for nothing |
| A price per variant | All sizes of a model share a price. Per-variant rules would be eight records where one belongs |
| A custom "shop stock" model | `stock.quant` per location already does this |
| Separate retail and wholesale products | Pricelists exist; duplicate products destroy inventory truth |
| A separate e-commerce catalog | `website_sale` sells the same `product.product` |
| A custom purchase receipt flow | `purchase.order` → `stock.picking` is exactly right |
| A custom pricing engine | `product.pricelist.item` with `min_quantity` covers the requirement |
| Writing quantities directly to `stock.quant` | Destroys the audit trail; inventory adjustments are the native path |
| A fiscal module that calls providers directly | Puts credentials and provider churn inside the ERP |
| One giant "Mati" addon | Nothing would be reusable for the next retailer |

### The one place we chose custom over vanilla, and why

**Fiscal output on the POS receipt.** The obvious approach is patching the POS
front-end receipt template. We used a server-side QWeb report instead, for two
reasons: fiscal registration completes *after* the sale, so the receipt printed
at payment time cannot contain an IRN that does not exist yet; and a bad xpath
into the POS front end breaks the point of sale, whereas a bad report only
breaks a report. What a shop actually needs is a reprintable fiscal receipt,
which is what we built. See `docs/fiscal-integration.md#receipt-output`.

## How this was verified

The matrix is not aspirational. Rows marked "Vanilla Odoo: Yes" are exercised
by `tools/verify_demo.py`, which runs inside Odoo against the real models and
asserts the exact quantities and prices — including that confirming a purchase
order does *not* move stock and that validating the receipt does.

Run it yourself:

```bash
make verify
```
