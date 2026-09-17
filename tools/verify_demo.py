"""End-to-end verification of the Mati retail platform.

Runs INSIDE Odoo (`odoo-bin shell`), so it exercises the real models, the real
stock engine and the real gateway over HTTP. Nothing is mocked.

It walks the mandatory vertical slice and asserts the exact numbers the demo
script quotes:

    supplier -> purchase order -> goods receipt -> main warehouse
    -> internal transfer -> shop 1 -> contextual retail price
    -> POS sale -> stock decrement -> fiscal transaction -> gateway
    -> mock fiscal provider -> IRN + QR -> stored in Odoo

then the resilience story:

    provider failure -> visible retryable state -> provider restored
    -> retry -> same idempotency identity -> exactly one registration

Usage (from the repository root):

    ./scripts/verify.sh

or directly:

    docker compose exec -T odoo odoo shell -d odoo --no-http < tools/verify_demo.py
"""

import json
import logging
import sys
import traceback

logging.getLogger("odoo").setLevel(logging.WARNING)

# `env` is injected by `odoo-bin shell`.
try:
    env  # noqa: B018
except NameError:  # pragma: no cover - only when run outside odoo shell
    print("This script must run inside `odoo-bin shell`.")
    raise SystemExit(2) from None

RESULTS = []
CONTEXT = {}

SKU = "SAM-BLK-42"
PURCHASE_QTY = 20
TRANSFER_QTY = 5

EXPECTED = {
    "opening_main": 10,
    "opening_shop1": 3,
    "after_receipt_main": 30,
    "after_transfer_main": 25,
    "after_transfer_shop1": 8,
    "after_sale_shop1": 7,
    "retail_price": 6000.0,
    "online_price": 6200.0,
    "wholesale_price": 5300.0,
    "wholesale_bulk_price": 5000.0,
    "vendor_price": 3200.0,
}


# ---------------------------------------------------------------------------
# Tiny assertion harness (no pytest inside Odoo shell)
# ---------------------------------------------------------------------------
def check(step, description, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    RESULTS.append({"step": step, "description": description, "status": status, "detail": str(detail)})
    symbol = "OK  " if condition else "FAIL"
    print(f"  [{symbol}] {description}" + (f"  -> {detail}" if detail else ""))
    return condition


def equals(step, description, actual, expected, tolerance=0.001):
    ok = abs(float(actual) - float(expected)) <= tolerance
    return check(step, description, ok, f"expected {expected}, got {actual}")


def section(title):
    print("")
    print("=" * 78)
    print(title)
    print("=" * 78)


def qty_at(variant, warehouse_code):
    warehouse = env["stock.warehouse"].search([("code", "=", warehouse_code)], limit=1)
    if not warehouse:
        return -1
    return env["stock.quant"]._get_available_quantity(variant, warehouse.lot_stock_id)


# ===========================================================================
# Step 1 - product identity
# ===========================================================================
def step_product():
    section("STEP 1  Product, variants, SKU and barcode")
    template = env["product.template"].search([("name", "=", "Adidas Samba")], limit=1)
    check(1, "product template 'Adidas Samba' exists", bool(template))
    if not template:
        return

    attributes = template.attribute_line_ids.mapped("attribute_id.name")
    check(1, "Color attribute is configured", "Color" in attributes, attributes)
    check(1, "Size attribute is configured", "Size" in attributes, attributes)
    equals(1, "4 variants were generated", len(template.product_variant_ids), 4)

    variant = env["product.product"].search([("default_code", "=", SKU)], limit=1)
    check(1, f"variant with SKU {SKU} exists", bool(variant))
    if not variant:
        return
    CONTEXT["variant"] = variant

    check(1, "variant has a barcode", bool(variant.barcode), variant.barcode)
    check(1, "barcode is 13 digits", len(variant.barcode or "") == 13, variant.barcode)

    # EAN-13 check digit must be correct, otherwise the barcode demo is a lie.
    barcode = variant.barcode or ""
    if len(barcode) == 13 and barcode.isdigit():
        total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(barcode[:12]))
        expected_check = (10 - total % 10) % 10
        check(1, "barcode check digit is valid", int(barcode[12]) == expected_check, barcode)

    # The barcode must resolve to THIS variant and nothing else.
    resolved = env["product.product"].search([("barcode", "=", variant.barcode)])
    equals(1, "barcode resolves to exactly one variant", len(resolved), 1)
    check(1, "barcode resolves to the right variant", resolved[:1] == variant, resolved.mapped("default_code"))

    all_variants = template.product_variant_ids
    check(
        1,
        "every variant has a unique SKU",
        len(set(all_variants.mapped("default_code"))) == len(all_variants),
        all_variants.mapped("default_code"),
    )
    check(
        1,
        "every variant has a unique barcode",
        len(set(all_variants.mapped("barcode"))) == len(all_variants),
        all_variants.mapped("barcode"),
    )
    check(1, "variant is sellable", variant.sale_ok)
    check(1, "variant is purchasable", variant.purchase_ok)
    check(1, "variant is stock-tracked", variant.is_storable)


# ===========================================================================
# Step 2 - inventory by location
# ===========================================================================
def step_inventory():
    section("STEP 2  Inventory distributed across locations")
    for code in ("MAIN", "SHOP1", "SHOP2"):
        warehouse = env["stock.warehouse"].search([("code", "=", code)], limit=1)
        check(2, f"warehouse {code} exists", bool(warehouse), warehouse.name if warehouse else "")

    variant = CONTEXT.get("variant")
    if not variant:
        return
    main = qty_at(variant, "MAIN")
    shop1 = qty_at(variant, "SHOP1")
    shop2 = qty_at(variant, "SHOP2")
    print(f"       {SKU}:  MAIN={main}  SHOP1={shop1}  SHOP2={shop2}")

    equals(2, f"{SKU} opening stock in main warehouse", main, EXPECTED["opening_main"])
    equals(2, f"{SKU} opening stock in shop 1", shop1, EXPECTED["opening_shop1"])
    check(2, "stock genuinely differs per location", len({main, shop1, shop2}) > 1)


# ===========================================================================
# Steps 3-5 - purchase, confirmation, receipt
# ===========================================================================
def step_purchase():
    section("STEP 3-5  Purchase order, confirmation and goods receipt")
    variant = CONTEXT.get("variant")
    if not variant:
        return

    supplier = env["res.partner"].search([("name", "=", "ABC Footwear Factory")], limit=1)
    check(3, "supplier 'ABC Footwear Factory' exists", bool(supplier))
    if not supplier:
        return

    vendor_price = env["product.supplierinfo"].search(
        [("partner_id", "=", supplier.id), ("product_id", "=", variant.id)], limit=1
    )
    check(3, f"vendor price exists for {SKU}", bool(vendor_price))
    if vendor_price:
        equals(3, f"vendor price for {SKU}", vendor_price.price, EXPECTED["vendor_price"])

    main_warehouse = env["stock.warehouse"].search([("code", "=", "MAIN")], limit=1)
    before = qty_at(variant, "MAIN")

    order = env["purchase.order"].create(
        {
            "partner_id": supplier.id,
            "picking_type_id": main_warehouse.in_type_id.id,
            "order_line": [
                (
                    0,
                    0,
                    {
                        "product_id": variant.id,
                        "product_qty": PURCHASE_QTY,
                        "price_unit": EXPECTED["vendor_price"],
                        "name": variant.display_name,
                        "product_uom": variant.uom_id.id,
                    },
                )
            ],
        }
    )
    check(3, f"purchase order created for {PURCHASE_QTY} x {SKU}", bool(order), order.name)
    CONTEXT["purchase_order"] = order

    # --- confirmation must NOT move stock -------------------------------
    order.button_confirm()
    env.cr.commit()
    check(4, "purchase order is confirmed", order.state == "purchase", order.state)

    after_confirm = qty_at(variant, "MAIN")
    equals(
        4,
        "confirming the PO does NOT increase on-hand stock",
        after_confirm,
        before,
    )

    picking = order.picking_ids[:1]
    check(4, "an incoming shipment was created", bool(picking), picking.name if picking else "")
    if not picking:
        return

    # --- receipt DOES move stock ----------------------------------------
    for move in picking.move_ids:
        move.quantity = move.product_uom_qty
        if hasattr(move, "picked"):
            move.picked = True
    picking.button_validate()
    env.cr.commit()

    check(5, "the receipt is validated", picking.state == "done", picking.state)
    after_receipt = qty_at(variant, "MAIN")
    print(f"       main warehouse: {before} -> {after_receipt}")
    equals(5, "receipt increased main warehouse stock", after_receipt, before + PURCHASE_QTY)
    equals(5, "main warehouse is at the expected level", after_receipt, EXPECTED["after_receipt_main"])
    CONTEXT["main_after_receipt"] = after_receipt


# ===========================================================================
# Step 6 - internal transfer
# ===========================================================================
def step_transfer():
    section("STEP 6  Internal transfer, main warehouse -> Shop 1")
    variant = CONTEXT.get("variant")
    if not variant:
        return

    main = env["stock.warehouse"].search([("code", "=", "MAIN")], limit=1)
    shop1 = env["stock.warehouse"].search([("code", "=", "SHOP1")], limit=1)
    if not main or not shop1:
        return

    main_before = qty_at(variant, "MAIN")
    shop1_before = qty_at(variant, "SHOP1")

    picking_type = main.int_type_id
    picking = env["stock.picking"].create(
        {
            "picking_type_id": picking_type.id,
            "location_id": main.lot_stock_id.id,
            "location_dest_id": shop1.lot_stock_id.id,
            "move_ids": [
                (
                    0,
                    0,
                    {
                        "name": variant.display_name,
                        "product_id": variant.id,
                        "product_uom_qty": TRANSFER_QTY,
                        "product_uom": variant.uom_id.id,
                        "location_id": main.lot_stock_id.id,
                        "location_dest_id": shop1.lot_stock_id.id,
                    },
                )
            ],
        }
    )
    picking.action_confirm()
    picking.action_assign()
    for move in picking.move_ids:
        move.quantity = TRANSFER_QTY
        if hasattr(move, "picked"):
            move.picked = True
    picking.button_validate()
    env.cr.commit()

    check(6, "internal transfer is validated", picking.state == "done", picking.state)
    main_after = qty_at(variant, "MAIN")
    shop1_after = qty_at(variant, "SHOP1")
    print(f"       main:   {main_before} -> {main_after}")
    print(f"       shop 1: {shop1_before} -> {shop1_after}")

    equals(6, "source location decreased", main_after, main_before - TRANSFER_QTY)
    equals(6, "destination location increased", shop1_after, shop1_before + TRANSFER_QTY)
    equals(6, "main warehouse is at the expected level", main_after, EXPECTED["after_transfer_main"])
    equals(6, "shop 1 is at the expected level", shop1_after, EXPECTED["after_transfer_shop1"])
    CONTEXT["shop1_before_sale"] = shop1_after


# ===========================================================================
# Step 7 - contextual pricing
# ===========================================================================
def step_pricing():
    section("STEP 7  Contextual pricing - one product, four prices")
    variant = CONTEXT.get("variant")
    if not variant:
        return

    prices = {}
    for key, name in (
        ("retail", "Retail Pricelist"),
        ("online", "Online Pricelist"),
        ("wholesale", "Wholesale Pricelist"),
    ):
        pricelist = env["product.pricelist"].search([("name", "=", name)], limit=1)
        check(7, f"pricelist '{name}' exists", bool(pricelist))
        if pricelist:
            prices[key] = pricelist._get_product_price(variant, 1.0)

    wholesale = env["product.pricelist"].search([("name", "=", "Wholesale Pricelist")], limit=1)
    if wholesale:
        prices["wholesale_bulk"] = wholesale._get_product_price(variant, 20.0)

    for key, value in prices.items():
        print(f"       {key:<16} {value:,.2f} ETB")

    if "retail" in prices:
        equals(7, "retail price", prices["retail"], EXPECTED["retail_price"])
    if "online" in prices:
        equals(7, "online price", prices["online"], EXPECTED["online_price"])
    if "wholesale" in prices:
        equals(7, "wholesale price", prices["wholesale"], EXPECTED["wholesale_price"])
    if "wholesale_bulk" in prices:
        equals(7, "wholesale bulk price at 20 units", prices["wholesale_bulk"], EXPECTED["wholesale_bulk_price"])

    check(
        7,
        "the same variant really does yield different prices",
        len(set(round(value, 2) for value in prices.values())) >= 3,
        prices,
    )

    # The decisive check: one product identity across every context.
    same_product = env["product.product"].search([("default_code", "=", SKU)])
    equals(7, "exactly one product record carries this SKU", len(same_product), 1)
    CONTEXT["retail_price"] = prices.get("retail", EXPECTED["retail_price"])


# ===========================================================================
# Step 8 - POS sale
# ===========================================================================
def pos_user_env():
    """An environment bound to a real user, not the superuser.

    ``pos.config.open_ui()`` refuses SUPERUSER_ID outright:

        if self.env.uid == SUPERUSER_ID and not tools.config['test_enable']:
            raise UserError("You do not have permission to open a POS session")

    That is correct behaviour - a session belongs to a cashier, not to
    OdooBot - and `odoo shell` runs as uid 1, so the POS steps act as `admin`.
    """
    admin = env.ref("base.user_admin", raise_if_not_found=False)
    if not admin:
        admin = env["res.users"].search([("login", "=", "admin")], limit=1)
    if not admin:
        return env
    group = env.ref("point_of_sale.group_pos_manager", raise_if_not_found=False)
    if group and group not in admin.groups_id:
        admin.sudo().write({"groups_id": [(4, group.id)]})
    return env(user=admin.id)


def step_pos_sale(expect_fiscal_failure=False):
    section("STEP 8  POS sale at Shop 1 and stock decrement")
    variant = CONTEXT.get("variant")
    if not variant:
        return None

    penv = pos_user_env()
    config = penv["pos.config"].search([("name", "=", "Shop 1 Retail POS")], limit=1)
    check(8, "Shop 1 Retail POS is configured", bool(config))
    if not config:
        return None

    shop1 = env["stock.warehouse"].search([("code", "=", "SHOP1")], limit=1)
    check(
        8,
        "POS is bound to the Shop 1 stock location",
        config.picking_type_id.warehouse_id == shop1,
        config.picking_type_id.warehouse_id.name,
    )
    check(
        8,
        "POS uses the retail pricelist",
        config.pricelist_id.name == "Retail Pricelist",
        config.pricelist_id.name,
    )

    # --- barcode resolution, exactly as a scan would do it ---------------
    scanned = env["product.product"].search(
        [("barcode", "=", variant.barcode), ("available_in_pos", "=", True)], limit=1
    )
    check(8, "scanning the barcode resolves to the right variant", scanned == variant, scanned.display_name)

    session = penv["pos.session"].search(
        [("config_id", "=", config.id), ("state", "in", ("opening_control", "opened"))], limit=1
    )
    if not session:
        # open_ui() is how the point of sale itself starts a session.
        config.open_ui()
        session = config.current_session_id
    if session.state == "opening_control":
        # set_opening_control() is the public API that actually opens the
        # session; it is what Odoo's own POS tests use. A session left in
        # 'opening_control' still accepts orders, which is why the sale below
        # succeeded even when this step was failing.
        session.set_opening_control(0, None)
    check(8, "a POS session is open", session.state == "opened", session.state)

    shop1_before = qty_at(variant, "SHOP1")
    price = config.pricelist_id._get_product_price(variant, 1.0)
    equals(8, "POS applies the retail price", price, EXPECTED["retail_price"])

    taxes = variant.taxes_id.filtered(lambda tax: tax.company_id == env.company)
    tax_result = taxes.compute_all(price, currency=config.currency_id, quantity=1.0, product=variant)
    subtotal = tax_result["total_excluded"]
    total = tax_result["total_included"]

    order = penv["pos.order"].create(
        {
            "company_id": env.company.id,
            "session_id": session.id,
            "pricelist_id": config.pricelist_id.id,
            "amount_tax": total - subtotal,
            "amount_total": total,
            "amount_paid": 0.0,
            "amount_return": 0.0,
            "lines": [
                (
                    0,
                    0,
                    {
                        "product_id": variant.id,
                        "full_product_name": variant.display_name,
                        "qty": 1.0,
                        "price_unit": price,
                        "discount": 0.0,
                        "price_subtotal": subtotal,
                        "price_subtotal_incl": total,
                        "tax_ids": [(6, 0, taxes.ids)],
                    },
                )
            ],
        }
    )
    payment_method = config.payment_method_ids[:1]
    check(8, "the POS has a payment method", bool(payment_method))
    if payment_method:
        penv["pos.payment"].create(
            {
                "pos_order_id": order.id,
                "payment_method_id": payment_method.id,
                "amount": total,
            }
        )

    # pos.order.amount_paid is a plain stored field, NOT computed from
    # pos.payment records - the POS front end sends it in the order payload.
    # Creating a payment therefore does not update it, and
    # action_pos_order_paid() would raise "Order ... is not fully paid".
    order.amount_paid = total

    order.action_pos_order_paid()
    if hasattr(order, "_create_order_picking"):
        order._create_order_picking()
    env.cr.commit()

    check(8, "the POS order is paid", order.state in ("paid", "done", "invoiced"), order.state)
    print(f"       order {order.name}: {total:,.2f} ETB (incl. tax)")

    shop1_after = qty_at(variant, "SHOP1")
    print(f"       shop 1 stock: {shop1_before} -> {shop1_after}")
    equals(8, "shop 1 stock decreased by exactly one", shop1_after, shop1_before - 1)

    main_now = qty_at(variant, "MAIN")
    equals(8, "the main warehouse was NOT touched by a shop sale", main_now, EXPECTED["after_transfer_main"])

    CONTEXT.setdefault("orders", []).append(order)
    return order


# ===========================================================================
# Steps 9-11 - fiscalization through the gateway
# ===========================================================================
def step_fiscal(order):
    section("STEP 9-11  Fiscal transaction, gateway and the fiscal result")
    if not order:
        return None

    transaction = env["et.fiscal.transaction"].search(
        [("source_model", "=", "pos.order"), ("source_record_id", "=", order.id)], limit=1
    )
    check(9, "a fiscal transaction was created for the sale", bool(transaction))
    if not transaction:
        return None

    check(9, "it links back to the POS order", transaction.source_record_id == order.id)
    check(9, "it has a stable idempotency key", bool(transaction.idempotency_key), transaction.idempotency_key)

    if transaction.state not in ("registered", "failed"):
        transaction.action_submit()
        env.cr.commit()

    print(f"       fiscal transaction {transaction.name}: state={transaction.state}")
    check(10, "the request reached the gateway", bool(transaction.gateway_document_id), transaction.gateway_document_id)
    check(11, "the document is registered", transaction.state == "registered", transaction.last_error or "")
    check(11, "an IRN was returned and stored", bool(transaction.irn), transaction.irn)
    check(11, "a QR payload was returned and stored", bool(transaction.qr_payload))
    check(11, "a provider reference was stored", bool(transaction.provider_transaction_id), transaction.provider_transaction_id)
    check(11, "the provider is recorded", bool(transaction.provider), transaction.provider)
    check(11, "the registration timestamp is stored", bool(transaction.registered_at))

    if transaction.irn:
        print(f"       IRN: {transaction.irn}")
        print(f"       provider reference: {transaction.provider_transaction_id}")

    CONTEXT["fiscal_transaction"] = transaction
    return transaction


# ===========================================================================
# Step 12 - duplicate protection
# ===========================================================================
def step_idempotency(transaction):
    section("STEP 12  Duplicate protection - same key, same IRN")
    if not transaction or transaction.state != "registered":
        check(12, "a registered transaction is available to re-submit", False)
        return

    original_irn = transaction.irn
    original_provider_reference = transaction.provider_transaction_id

    # Re-submitting must not register a second document.
    transaction.action_refresh_from_gateway()
    env.cr.commit()
    check(12, "re-reading the gateway returns the same IRN", transaction.irn == original_irn, transaction.irn)

    transaction._submit()
    env.cr.commit()
    check(12, "re-submitting does not change the IRN", transaction.irn == original_irn, transaction.irn)
    check(
        12,
        "re-submitting does not change the provider reference",
        transaction.provider_transaction_id == original_provider_reference,
        transaction.provider_transaction_id,
    )
    check(12, "the transaction is still registered", transaction.state == "registered", transaction.state)

    duplicates = env["et.fiscal.transaction"].search(
        [
            ("source_model", "=", transaction.source_model),
            ("source_record_id", "=", transaction.source_record_id),
        ]
    )
    equals(12, "the source order still has exactly one fiscal transaction", len(duplicates), 1)


# ===========================================================================
# Step 13 - failure and recovery
# ===========================================================================
def gateway_admin(path, payload=None):
    """Call the gateway admin API using Odoo's configured credentials."""
    import requests

    client = env["et.fiscal.gateway.client"]
    config = client._get_config()
    headers = {"X-API-Key": config["api_key"], "Content-Type": "application/json"}
    url = f"{config['base_url']}{path}"
    if payload is None:
        response = requests.get(url, headers=headers, timeout=config["timeout"])
    else:
        response = requests.post(
            url, data=json.dumps(payload), headers=headers, timeout=config["timeout"]
        )
    response.raise_for_status()
    return response.json()


def step_failure_and_recovery():
    section("STEP 13  Provider failure, retryable state and recovery")

    try:
        gateway_admin("/api/v1/admin/mock/fiscal/failure-mode", {"enabled": True})
        check(13, "the mock fiscal provider was forced into failure mode", True)
    except Exception as exc:  # noqa: BLE001
        check(13, "the mock fiscal provider was forced into failure mode", False, exc)
        return

    variant = CONTEXT.get("variant")
    shop1_before = qty_at(variant, "SHOP1") if variant else None

    order = step_pos_sale(expect_fiscal_failure=True)
    if not order:
        gateway_admin("/api/v1/admin/mock/fiscal/failure-mode", {"enabled": False})
        return

    transaction = env["et.fiscal.transaction"].search(
        [("source_model", "=", "pos.order"), ("source_record_id", "=", order.id)], limit=1
    )
    check(13, "a fiscal transaction exists despite the outage", bool(transaction))
    if not transaction:
        gateway_admin("/api/v1/admin/mock/fiscal/failure-mode", {"enabled": False})
        return

    if transaction.state not in ("failed", "registered"):
        transaction.action_submit()
        env.cr.commit()

    check(13, "the fiscal state is visibly failed", transaction.state == "failed", transaction.state)
    check(13, "the failure reason is visible", bool(transaction.last_error), transaction.last_error)
    check(13, "the document is still retryable", transaction.is_retryable)

    # The sale itself is unaffected: this is the whole point of the boundary.
    shop1_after = qty_at(variant, "SHOP1") if variant else None
    check(13, "the sale completed normally", order.state in ("paid", "done", "invoiced"), order.state)
    if shop1_before is not None:
        equals(13, "stock still decremented correctly during the outage", shop1_after, shop1_before - 1)

    failed_key = transaction.idempotency_key

    # --- restore the provider and retry ---------------------------------
    gateway_admin("/api/v1/admin/mock/fiscal/failure-mode", {"enabled": False})
    check(13, "the provider was restored", True)

    transaction.action_retry()
    env.cr.commit()

    check(13, "the retry registered the document", transaction.state == "registered", transaction.last_error or "")
    check(13, "an IRN was assigned on retry", bool(transaction.irn), transaction.irn)
    check(13, "the idempotency key never changed", transaction.idempotency_key == failed_key)

    # Retry again: still exactly one registration.
    irn_after_retry = transaction.irn
    transaction._submit()
    env.cr.commit()
    check(13, "retrying again yields the same IRN", transaction.irn == irn_after_retry, transaction.irn)

    all_for_order = env["et.fiscal.transaction"].search(
        [("source_model", "=", "pos.order"), ("source_record_id", "=", order.id)]
    )
    equals(13, "exactly one fiscal registration exists for this sale", len(all_for_order), 1)

    # The gateway's audit trail must show the whole story, failures included.
    if transaction.gateway_document_id:
        try:
            audit = gateway_admin(
                f"/api/v1/admin/audit/fiscal_document/{transaction.gateway_document_id}"
            )
            outcomes = [attempt["outcome"] for attempt in audit["attempts"]]
            print(f"       gateway attempts: {outcomes}")
            check(13, "failed attempts are preserved in the audit trail", "transient_error" in outcomes, outcomes)
            check(13, "the successful attempt is recorded", "success" in outcomes, outcomes)
        except Exception as exc:  # noqa: BLE001
            check(13, "the gateway audit trail is readable", False, exc)


# ===========================================================================
# Extension seams
# ===========================================================================
def step_extension_seams():
    section("EXTENSION SEAMS  Payment, delivery and provider replaceability")

    provider = env["payment.provider"].search([("code", "=", "integration_gateway")], limit=1)
    check("seams", "an Integration Gateway payment provider exists", bool(provider))

    carrier = env["delivery.carrier"].search([("delivery_type", "=", "integration_gateway")], limit=1)
    check("seams", "an Integration Gateway delivery carrier exists", bool(carrier))

    try:
        client = env["et.fiscal.gateway.client"]
        health = client.health()
        providers = health.get("providers", {})
        print(f"       gateway providers: {providers}")
        check("seams", "the gateway is healthy", health.get("status") == "pass", health.get("status"))
        check("seams", "a fiscal provider is selected by configuration", bool(providers.get("fiscal")), providers.get("fiscal"))
        check("seams", "a payment provider is selected by configuration", bool(providers.get("payment")), providers.get("payment"))
        check("seams", "a delivery provider is selected by configuration", bool(providers.get("delivery")), providers.get("delivery"))
    except Exception as exc:  # noqa: BLE001
        check("seams", "the gateway health endpoint is reachable", False, exc)


# ===========================================================================
# Inventory reconciliation invariant
# ===========================================================================
def step_reconciliation():
    section("INVARIANT  Inventory is fully explained by stock moves")
    variant = CONTEXT.get("variant")
    if not variant:
        return

    for code in ("MAIN", "SHOP1"):
        warehouse = env["stock.warehouse"].search([("code", "=", code)], limit=1)
        if not warehouse:
            continue
        location = warehouse.lot_stock_id
        on_hand = env["stock.quant"]._get_available_quantity(variant, location)

        # Note: no "not child_of" clause - Odoo has no such operator. It is not
        # needed either: a move entirely inside this location appears in both
        # sums and cancels out, which is exactly right.
        incoming = sum(
            env["stock.move"]
            .search(
                [
                    ("product_id", "=", variant.id),
                    ("state", "=", "done"),
                    ("location_dest_id", "child_of", location.id),
                ]
            )
            .mapped("quantity")
        )
        outgoing = sum(
            env["stock.move"]
            .search(
                [
                    ("product_id", "=", variant.id),
                    ("state", "=", "done"),
                    ("location_id", "child_of", location.id),
                ]
            )
            .mapped("quantity")
        )
        computed = incoming - outgoing
        print(f"       {code}: in {incoming} - out {outgoing} = {computed}  (on hand {on_hand})")
        equals(
            "invariant",
            f"{code} on-hand equals inbound minus outbound moves",
            on_hand,
            computed,
        )


# ===========================================================================
# Main
# ===========================================================================
def main():
    print("")
    print("#" * 78)
    print("#  MATI RETAIL PLATFORM - END TO END VERIFICATION")
    print("#  DEMO FISCAL REGISTRATION - NOT PRODUCTION CERTIFICATION")
    print("#" * 78)

    try:
        step_product()
        step_inventory()
        step_purchase()
        step_transfer()
        step_pricing()
        order = step_pos_sale()
        transaction = step_fiscal(order)
        step_idempotency(transaction)
        step_failure_and_recovery()
        step_extension_seams()
        step_reconciliation()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        RESULTS.append(
            {
                "step": "fatal",
                "description": "verification crashed",
                "status": "FAIL",
                "detail": traceback.format_exc()[-2000:],
            }
        )

    passed = sum(1 for result in RESULTS if result["status"] == "PASS")
    failed = sum(1 for result in RESULTS if result["status"] == "FAIL")

    section("SUMMARY")
    if failed:
        print("Failed checks:")
        for result in RESULTS:
            if result["status"] == "FAIL":
                print(f"  - [step {result['step']}] {result['description']}: {result['detail']}")
        print("")
    print(f"  passed: {passed}")
    print(f"  failed: {failed}")
    print("")

    try:
        with open("/tmp/verify_report.json", "w", encoding="utf-8") as handle:
            json.dump({"passed": passed, "failed": failed, "results": RESULTS}, handle, indent=2)
        print("  full report written to /tmp/verify_report.json")
    except OSError:
        pass

    print("")
    print(f"VERIFY RESULT: {'PASS' if failed == 0 else 'FAIL'}")
    print("")
    env.cr.commit()
    return failed == 0


_ok = main()
sys.stdout.flush()
