# Demo script — Mati's Shoes

> A business walkthrough. Written to be read out loud while sitting next to
> Mati, not by a developer to a developer.

**Before you start**

```bash
make up && make bootstrap      # first time
make demo-reset                # every rehearsal after that
```

Odoo: <http://localhost:8069> (`admin` / `admin`) · Gateway: <http://localhost:8000>

Everything is reachable from the **Mati Demo** menu: Demo Controls, Integration
Status, Fiscal Transactions, Products, Stock by Shop, Purchase Orders.

> **Say this once, at the start.** Fiscal registration, card payment and
> delivery all run against *mock* providers. The plumbing is real; the
> providers are not. Nothing here is a legally valid Ethiopian fiscal
> registration, and we are not claiming certification.

---

## Part A — How a shoe is defined

**Mati Demo → Products**, open **Zala 2147**.

| | |
|---|---|
| Factory | Zala |
| Model | 2147 |

> "You buy from a factory, and each factory has model numbers. So the product
> is **the factory plus the model** — Zala 2147. That's one product."

Now the **Variants** tab — four of them:

```
Zala 2147 / Black / 39
Zala 2147 / Black / 40
Zala 2147 / White / 39
Zala 2147 / White / 40
```

> "Colour and size are what actually sit in the box. Those are the things you
> count, and those are the things the barcode is stuck to. One product, four
> real pairs."

Open **Black / 39**:

| | |
|---|---|
| Internal Reference (SKU) | `ZALA-2147-BLK-39` |
| Barcode | `2000003900008` |

> "The SKU is how you and I talk about it. The barcode is what the scanner
> reads. You generate that barcode in your label software and stick it on the
> box — Odoo just needs to hold the same number against this exact pair. Not
> against 'Zala 2147' in general. Against black, size 39."

Group the product list by **Factory** to show the catalogue the way he buys it.

---

## Part B — Why the same shoe has two prices

**Sales → Products → Pricelists**, then back to the variant.

| Context | Zala 2147 |
|---|---|
| Retail | **6,000 ETB** |
| Wholesale | **5,200 ETB** |

> "Two prices, one shoe. There is still exactly one product record and one
> stock number. Nobody had to create a 'wholesale Zala' that slowly drifts out
> of step with the retail one."

Show that every size is the same price:

```
Black/39   6,000 retail   5,200 wholesale
Black/40   6,000          5,200
White/39   6,000          5,200
White/40   6,000          5,200
```

> "You price by model, not by size — so that's how it's set up. If you ever
> need one size priced differently, it's a rule, not a new product."

---

## Part C — What's in each shop

On the variant, click **On Hand** and group by Location.

| | Zala 2147 / Black / 39 |
|---|---|
| Shop 1 | **12** |
| Shop 2 | **5** |

> "Same shoe, two shops, two separate numbers. One business, one TIN — but
> each shop's stock is its own. When Shop 1 sells a pair, Shop 2's number does
> not move."

---

## Part D — Buying from the factory

**Mati Demo → Purchase Orders → New**

- Vendor: **ABC Footwear Factory**
- Product: **Zala 2147 / Black / 39**
- Quantity: **20**
- **Deliver To: Shop 1**

Odoo fills in **3,400 ETB** — the agreed price with that supplier.

> "The shoes come from the factory straight to the shop that ordered them.
> There's no central store in the middle, because that's not how you work."

Click **Confirm Order**, then check Shop 1's stock again. **Still 12.**

> "This is the important one. You have *ordered* twenty pairs. You do not
> *have* them. Nothing in your stock changed, because nothing arrived at Shop
> 1 yet. A system that added twenty here would be lying to you."

Now open the **Receipt** from the order and click **Validate**.

```
Shop 1:  12  →  32
```

> "*Now* you have them. Stock moved when the goods arrived, not when the order
> was placed. And Shop 2 is untouched — those twenty pairs went to Shop 1."

---

## Part E — Selling one at Shop 1

**Point of Sale → Shop 1 POS → New Session**. Scan `2000003900008`.

- the right variant appears: **Zala 2147 (Black, 39)**
- the price is **6,000 ETB** — this till is on the retail pricelist

Take payment and validate.

```
Shop 1:  32  →  31
```

> "The cashier scanned, took the money, and Shop 1's stock went down by exactly
> one. Nobody typed a stock number. And Shop 2 didn't move — you sold from
> *this* shop's shelf."

---

## Part F — Selling the same shoe wholesale

**Point of Sale → Shop 2 POS**. Scan the **same barcode**.

- the same variant appears
- the price is **5,200 ETB** — this till defaults to wholesale

> "Same shoe. Same product record. Same barcode. Different price, because it's
> a different kind of sale. Nothing was duplicated to make that happen."

Worth adding: both tills can reach **both** pricelists, so a wholesale
customer walking into Shop 1 is a pricelist choice at the till, not a
workaround.

---

## Part G — What happens after the sale, for tax

**Mati Demo → Fiscal Transactions** (or the Fiscalization tab on the POS order).

The sale created its own fiscal record, which moved:

```
pending → submitting → registered
```

| | |
|---|---|
| IRN | `ET-DEMO-2026-…` |
| Provider | mock |
| QR code | on the QR tab |

> "The sale is finished and the money is in the till. The tax registration is a
> separate conversation with a separate system, so it's a separate record. It
> came back with a registration number and a QR code, and those are now stored
> against that sale permanently."

Then, on the POS order, **Print Fiscal Receipt** — the IRN and QR are on the
printed document.

---

## Part H — Why the plumbing is built this way

**Mati Demo → Integration Status**.

```
Fiscal provider     mock
Payment provider    mock
Delivery provider   mock
```

> "Right now all three of these are simulators. When you sign with a real
> payment provider, or when a real tax provider is available, we change *this
> setting* and give it the credentials. We do not rebuild your products, your
> stock, your purchasing or your tills. That's the whole reason it's built in
> two pieces."

If he asks what that looks like:

```bash
PAYMENT_PROVIDER=mock   →   PAYMENT_PROVIDER=provider_a
```

plus that provider's credentials, then restart one service.

---

## Part I — What if the tax system is down?

This is the question every Ethiopian retailer actually has. Do it slowly.

**Break it on purpose:** Demo Controls → **Toggle Mock Fiscal Failure Mode**
(or `make fiscal-fail`).

**Now make another sale** at Shop 1, exactly as in Part E.

Show three things, in this order:

1. **the sale completed** — the customer paid and left with the shoes;
2. **the stock is correct** — Shop 1 went down by one again;
3. **the fiscal record says `Failed`**, with the reason visible, and it is
   marked retryable.

> "The tax system being down did not stop you selling shoes. That is the whole
> point. Your business doesn't stop because someone else's server is having a
> bad day."

**Fix it:** toggle failure mode off (`make fiscal-ok`), then click **Retry** on
the failed record.

It goes to **Registered** with an IRN. Click **Retry** again — the IRN does not
change.

> "It caught up by itself. And it used the same identity it was given when the
> sale happened, so even though we tried several times, there is exactly one
> registration for that sale. Not one per attempt. That matters — you'd be
> declaring revenue you never earned."

In normal running this retry is automatic: a scheduled job drains the backlog
every five minutes.

---

## The questions this answers

| Question | Part |
|---|---|
| Where is Zala 2147 Black 39 defined? | A |
| What barcode identifies it? | A |
| Why is Factory not a size or colour? | A |
| Why does it cost one price retail and another wholesale? | B |
| How many pairs are in each shop? | C |
| Where did these 20 new pairs come from? | D |
| When did they become real inventory? | D |
| What happens when the cashier scans and sells? | E |
| Did Shop 1's stock fall — and only Shop 1's? | E |
| Can I sell the same shoe wholesale without duplicating it? | F |
| What happens after the sale for tax? | G |
| Can we switch payment/fiscal/delivery provider later? | H |
| What if the fiscal service is unavailable? | I |

---

## Closing

> "Everything you saw about products, stock, buying, pricing and selling is
> standard Odoo. We configured it for how you actually work — factory and
> model, two shops, retail and wholesale. We didn't write it, which means it
> keeps working after an upgrade and it isn't ours to break.
>
> The only things we built are the three that connect you to Ethiopian
> systems: tax registration, payments, and delivery. Each sits behind a
> boundary, so any of them can be swapped for the real thing with a setting
> and a set of credentials — without touching your shops."

---

## Reset between rehearsals

```bash
make demo-reset
```

Restores opening stock, cancels demo purchase orders, closes POS sessions and
puts the mock providers back to working. Fiscal transactions are deliberately
kept — deleting a registration history is exactly what a fiscal system must
never do.

## Prove it without clicking

```bash
make verify
```

Walks Parts A–I against the real database and the real gateway, asserting every
number quoted above. Exits non-zero if anything is wrong.
