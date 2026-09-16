# Demo script — Mati's Shoes

> A business walkthrough. Written to be read out loud while sitting next to
> Mati, not to be read by a developer.

**The story in one sentence:** this shoe is bought from a supplier, received
into the main warehouse, transferred to the retail shop, sold at the shop's
retail price, removed from that shop's inventory, and then sent through the
fiscal registration path.

**Before you start**

```bash
make up && make bootstrap
```

Odoo is at <http://localhost:8069> (login `admin`, password `admin`).
The Integration Gateway is at <http://localhost:8000>.

If you have run the demo before: `make reset`.

> **Say this out loud once, at the start:** the fiscal registration in this
> demo goes to a *mock* provider. It proves the plumbing works. It is not a
> legally valid Ethiopian fiscal registration and we are not claiming
> certification.

---

## Step 1 — Where is this shoe defined?

**Inventory → Products → Products**, open **Adidas Samba**.

Show:

- one product, with two attributes: **Color** (Black, White) and **Size** (41, 42);
- the **Variants** tab: Odoo generated **four** concrete shoes.

> "You sell one model, but you stock four actual pairs. Odoo knows the
> difference. This is one product record, not four."

Open the variant **Adidas Samba (Black, 42)**:

| | |
|---|---|
| Internal Reference (SKU) | `SAM-BLK-42` |
| Barcode | `2000004200008` |

> "The SKU is how you talk about it. The barcode is what the scanner reads.
> They are different things and both point at this exact pair — black,
> size 42. Not at 'Adidas Samba' in general."

---

## Step 2 — How many pairs are in each shop?

Still on the variant, click the **On Hand** button, group by **Location**.

| Location | SAM-BLK-42 |
|---|---|
| Main Warehouse | **10** |
| Shop 1 Retail | **3** |
| Shop 2 Wholesale | **6** |

> "This is the same pair of shoes in three places. Not three products — three
> locations holding the same product."

---

## Step 3 — Where do new pairs come from?

**Purchase → Orders → Purchase Orders → New**.

- Vendor: **ABC Footwear Factory**
- Product: **Adidas Samba (Black, 42)**
- Quantity: **20**

Odoo fills in the price: **3,200 ETB** — the price agreed with that supplier for
that exact variant.

> "You buy at 3,200 and sell at 6,000. Odoo already knows your supplier price,
> per size and colour."

---

## Step 4 — Confirming the order does *not* create stock

Click **Confirm Order**.

Now go back and check the on-hand quantity for SAM-BLK-42 in the main
warehouse. It is still **10**.

> "This is important. You have *ordered* 20 pairs. You do not *have* them.
> Nothing in your inventory changed, because nothing arrived at your door yet.
> A system that increased your stock here would be lying to you."

Point at the new **Receipt** button on the purchase order: Odoo created an
incoming shipment that is waiting.

---

## Step 5 — Receiving the goods

Open the **Receipt** from the purchase order and click **Validate**.

Check the main warehouse again:

```
Main Warehouse:  10  →  30
```

> "*Now* you have them. Stock moved the moment the goods were received, not
> when the order was placed. Every single pair in that number can be traced
> back to the receipt that brought it in."

---

## Step 6 — Getting shoes to the shop

**Inventory → Operations → Internal Transfers → New**.

- From: **Main Warehouse / Stock**
- To: **Shop 1 Retail / Stock**
- Product: **Adidas Samba (Black, 42)**, quantity **5**

Mark as done and **Validate**.

| | Before | After |
|---|---|---|
| Main Warehouse | 30 | **25** |
| Shop 1 Retail | 3 | **8** |

> "Five pairs left the warehouse and five arrived at Shop 1. Both sides moved.
> Nothing appeared from nowhere and nothing vanished."

---

## Step 7 — Why does the same shoe have different prices?

**Sales → Products → Pricelists**. Show the three lists, then the same variant
in each:

| Context | Price for SAM-BLK-42 |
|---|---|
| Retail | **6,000 ETB** |
| Online | **6,200 ETB** |
| Wholesale | **5,300 ETB** |
| Wholesale, 20 pairs or more | **5,000 ETB** |

> "Four prices. One shoe. There is still exactly one product record and one
> stock number — nobody had to create a 'wholesale Adidas Samba' that then
> drifts out of sync with the retail one. The price depends on *who is buying
> and how*, not on which product you picked."

This is the answer to "can I sell the same thing at different prices to
different customers?" — yes, and without duplicating anything.

---

## Step 8 — Selling it in the shop

**Point of Sale → Shop 1 Retail POS → New Session**.

Scan or search the barcode `2000004200008`.

- the correct variant appears: **Adidas Samba (Black, 42)** — not the white
  one, not size 41;
- the price shown is **6,000 ETB**, because this till is on the retail
  pricelist.

Take payment and validate the order.

Now check Shop 1's stock for that variant:

```
Shop 1 Retail:  8  →  7
```

> "The cashier scanned, took the money, and Shop 1's stock went down by exactly
> one. Nobody typed a stock number. And notice the main warehouse did not
> change — you sold from *this shop's* shelf."

---

## Step 9 — What happens after the sale, for tax?

**Fiscalization → Fiscal Transactions** (or the **Fiscalization** tab on the
POS order).

A fiscal transaction was created for that sale, and it moved through:

```
pending  →  submitting  →  registered
```

> "The sale created a separate fiscal record. It has its own life: the sale is
> finished, but the tax registration is a separate conversation with a separate
> system."

---

## Step 10 — Where did the request go?

Open the fiscal transaction and show the **Request Sent** tab.

> "Odoo did not talk to the tax system directly. It handed a standard document
> to our Integration Gateway, and the gateway talked to the fiscal provider.
> That is deliberate — I'll come back to why it matters."

You can show the gateway itself at <http://localhost:8000/docs> if the audience
is technical.

---

## Step 11 — The fiscal result

Back on the fiscal transaction:

| | |
|---|---|
| Status | **Registered** |
| IRN | `ET-DEMO-2026-000001` |
| Provider reference | `fp_00000001` |
| Registered at | (timestamp) |
| QR code | shown on the **QR Code** tab |

Then, on the POS order, click **Print Fiscal Receipt** — the IRN and QR code are
on the printed document.

> "This came back from the fiscal provider and is now stored against the sale,
> permanently. Again — this is a demo provider. The shape is real, the
> registration is not legally valid."

---

## Step 12 — What if the same sale gets sent twice?

On the fiscal transaction, click **Refresh from Gateway**, then submit again.

The IRN does not change. There is still one registration.

> "Systems retry. Networks hiccup. Somebody clicks twice. None of that can
> register your sale to the tax authority twice, which would be a real problem
> for you — you'd be declaring revenue you never earned."

---

## Step 13 — What if the tax system is down?

This is the part worth doing slowly, because it is the question every Ethiopian
retailer actually has.

**Break it on purpose.** In Odoo: **Mati Demo → Demo Controls → Toggle Mock
Fiscal Failure Mode**. (Or from a terminal: `make fiscal-fail`.)

**Now make another sale** in Shop 1, exactly as in step 8.

Show three things, in this order:

1. **the sale completed** — the customer paid and walked out with the shoes;
2. **the stock is correct** — Shop 1 went down by one again;
3. **the fiscal transaction is `Failed`**, with the reason visible, and marked
   retryable.

> "The tax system being down did not stop you selling shoes. That is the whole
> point. Your business does not stop because someone else's server is having a
> bad day."

**Fix it.** Toggle failure mode back off (or `make fiscal-ok`).

On the failed fiscal transaction, click **Retry**.

It goes to **Registered** with an IRN.

> "It caught up by itself. And critically — it used the *same* identity it was
> given when the sale happened, so even though we tried several times, there is
> exactly one registration for that sale. Not one per attempt."

Click **Retry** once more to show the IRN still does not change.

In production this retry is automatic: a scheduled job drains the backlog every
five minutes.

---

## The questions this demo answers

| Question | Where it was answered |
|---|---|
| Where is Adidas Samba Black 42 defined? | Step 1 |
| What barcode identifies it? | Step 1 |
| How many pairs exist in each shop? | Step 2 |
| Where did these 20 new pairs come from? | Step 3 |
| When did they become actual inventory? | Steps 4–5 |
| How did five pairs reach Shop 1? | Step 6 |
| Why does it cost one amount retail and another wholesale? | Step 7 |
| What happens when the cashier scans and sells it? | Step 8 |
| Did Shop 1's stock automatically fall? | Step 8 |
| What happens after the sale for fiscal registration? | Steps 9–11 |
| What if the external fiscal service is unavailable? | Step 13 |
| Can we change payment, fiscal or delivery provider without replacing Odoo? | Below |

---

## Closing: the architecture question

> "Everything you just saw about products, stock, buying, moving, pricing and
> selling is standard Odoo. We configured it for your business; we did not
> write it. That matters, because it means it is maintained by someone other
> than us and it will still work after an upgrade.
>
> The only things we built are the three that connect you to *Ethiopian*
> systems: tax registration, payment providers, and delivery couriers. Each of
> those sits behind a boundary. When you want to switch from one payment
> provider to another — ArifPay, Chapa, Telebirr — we change one setting on the
> gateway. We do not touch Odoo, we do not migrate your data, and your shops
> do not stop selling.
>
> And when a real accredited fiscal provider is available, the same is true:
> that swap happens at the gateway, not in your ERP."

---

## Reset between demos

```bash
make reset
```

Restores the opening stock and clears demo purchase orders and transfers.
Fiscal transactions are deliberately kept — deleting a registration history is
exactly what a fiscal system must never do.
