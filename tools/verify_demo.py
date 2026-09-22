"""End-to-end verification of the Mati retail platform.

Runs INSIDE Odoo (`odoo-bin shell`), so it exercises the real models, the real
stock engine and the real gateway over HTTP. Nothing is mocked except the
external providers themselves.

It walks the demo script (docs/demo-script.md) and asserts the exact numbers
it quotes:

    A  product     Factory + Model -> template, Colour + Size -> variants
    B  pricing     retail vs wholesale, one product identity
    C  shop stock  Shop 1 and Shop 2 hold the same variant independently
    D  purchase    supplier -> Shop 1 directly, PO confirm != receipt
    E  retail      POS sale at Shop 1, stock decrements at Shop 1 only
    F  wholesale   same variant, wholesale price, no duplicate product
    G  fiscal      gateway -> mock provider -> IRN + QR stored in Odoo
    H  providers   active rails readable, swappable by configuration
    I  recovery    provider outage -> retryable -> retry -> one registration

Usage (from the repository root):

    ./scripts/verify.sh
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

SKU = "ZALA-2147-BLK-39"
TEMPLATE_NAME = "Zala 2147"
PURCHASE_QTY = 20

EXPECTED = {
    "opening_shop1": 12,
    "opening_shop2": 5,
    "after_receipt_shop1": 32,
    "after_sale_shop1": 31,
    "retail_price": 6000.0,
    "wholesale_price": 5200.0,
    "vendor_price": 3400.0,
    "variants_per_template": 4,
}


# ---------------------------------------------------------------------------
# Tiny assertion harness (no pytest inside an Odoo shell)
# ---------------------------------------------------------------------------
def check(step, description, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    RESULTS.append({"step": step, "description": description, "status": status, "detail": str(detail)})
    print(f"  [{'OK  ' if condition else 'FAIL'}] {description}" + (f"  -> {detail}" if detail else ""))
    return condition


def equals(step, description, actual, expected, tolerance=0.001):
    ok = abs(float(actual) - float(expected)) <= tolerance
    return check(step, description, ok, f"expected {expected}, got {actual}")


def section(title):
    print("")
    print("=" * 78)
    print(title)
    print("=" * 78)


def qty_at(variant, shop_code):
    warehouse = env["stock.warehouse"].search([("code", "=", shop_code)], limit=1)
    if not warehouse:
        return -1
    return env["stock.quant"]._get_available_quantity(variant, warehouse.lot_stock_id)


def pos_user_env():
    """An environment bound to a real user, not the superuser.

    ``pos.config.open_ui()`` refuses SUPERUSER_ID outright - a session belongs
    to a cashier, not to OdooBot - and `odoo shell` runs as uid 1.
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


def gateway_admin(path, payload=None):
    """Call the gateway admin API using Odoo's configured credentials."""
    import requests

    config = env["et.fiscal.gateway.client"]._get_config()
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


# ===========================================================================
# Part A - product identity
# ===========================================================================
def part_a_product():
    section("PART A  Factory + Model = product, Colour + Size = variants")

    company = env.company
    check("A", "one legal company", bool(company), company.name)
    check("A", "company has a TIN", bool(company.et_fiscal_tin), company.et_fiscal_tin)
    equals("A", "exactly one company is used", env["res.company"].search_count([]), 1)

    template = env["product.template"].search([("name", "=", TEMPLATE_NAME)], limit=1)
    check("A", f"product template '{TEMPLATE_NAME}' exists", bool(template))
    if not template:
        return

    check("A", "Factory is stored on the template", template.shoe_factory == "Zala", template.shoe_factory)
    check("A", "Model is stored on the template", template.shoe_model == "2147", template.shoe_model)

    attributes = set(template.attribute_line_ids.mapped("attribute_id.name"))
    check("A", "Colour is a variant attribute", "Color" in attributes, attributes)
    check("A", "Size is a variant attribute", "Size" in attributes, attributes)
    check("A", "Factory is NOT a variant attribute", "Factory" not in attributes, attributes)
    equals("A", "variants generated", len(template.product_variant_ids), EXPECTED["variants_per_template"])

    variant = env["product.product"].search([("default_code", "=", SKU)], limit=1)
    check("A", f"variant {SKU} exists", bool(variant))
    if not variant:
        return
    CONTEXT["variant"] = variant
    CONTEXT["template"] = template

    check("A", "variant has a barcode", bool(variant.barcode), variant.barcode)
    barcode = variant.barcode or ""
    if len(barcode) == 13 and barcode.isdigit():
        total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(barcode[:12]))
        check("A", "barcode check digit is valid", int(barcode[12]) == (10 - total % 10) % 10, barcode)

    resolved = env["product.product"].search([("barcode", "=", variant.barcode)])
    equals("A", "barcode resolves to exactly one variant", len(resolved), 1)
    check("A", "barcode resolves to the right variant", resolved[:1] == variant, resolved.mapped("default_code"))
    check("A", "SKU and barcode are different identifiers", variant.default_code != variant.barcode)

    all_variants = template.product_variant_ids
    check("A", "every variant has a unique SKU",
          len(set(all_variants.mapped("default_code"))) == len(all_variants),
          all_variants.mapped("default_code"))
    check("A", "every variant has a unique barcode",
          len(set(all_variants.mapped("barcode"))) == len(all_variants))
    check("A", "variant is sellable and purchasable", variant.sale_ok and variant.purchase_ok)
    check("A", "variant is stock-tracked", variant.is_storable)


# ===========================================================================
# Part B - pricing
# ===========================================================================
def part_b_pricing():
    section("PART B  Retail and wholesale - one product identity")
    variant = CONTEXT.get("variant")
    template = CONTEXT.get("template")
    if not variant or not template:
        return

    prices = {}
    for key, name in (("retail", "Retail Pricelist"), ("wholesale", "Wholesale Pricelist")):
        pricelist = env["product.pricelist"].search([("name", "=", name)], limit=1)
        check("B", f"pricelist '{name}' exists", bool(pricelist))
        if pricelist:
            prices[key] = pricelist._get_product_price(variant, 1.0)
            CONTEXT[f"pricelist_{key}"] = pricelist

    for key, value in prices.items():
        print(f"       {key:<12} {value:,.2f} ETB")

    if "retail" in prices:
        equals("B", "retail price", prices["retail"], EXPECTED["retail_price"])
    if "wholesale" in prices:
        equals("B", "wholesale price", prices["wholesale"], EXPECTED["wholesale_price"])
    check("B", "retail and wholesale differ",
          abs(prices.get("retail", 0) - prices.get("wholesale", 0)) > 0.01)

    # Every colour/size of this model shares the same price.
    retail = CONTEXT.get("pricelist_retail")
    if retail:
        per_variant = {
            v.default_code: retail._get_product_price(v, 1.0) for v in template.product_variant_ids
        }
        check("B", "all variants of the model share one retail price",
              len(set(round(p, 2) for p in per_variant.values())) == 1, per_variant)

    same_sku = env["product.product"].search([("default_code", "=", SKU)])
    equals("B", "exactly one product record carries this SKU", len(same_sku), 1)


# ===========================================================================
# Part C - stock per shop
# ===========================================================================
def part_c_shop_stock():
    section("PART C  Shop 1 and Shop 2 hold stock independently")
    variant = CONTEXT.get("variant")
    if not variant:
        return

    warehouses = env["stock.warehouse"].search([])
    codes = set(warehouses.mapped("code"))
    check("C", "Shop 1 exists", "SHOP1" in codes, codes)
    check("C", "Shop 2 exists", "SHOP2" in codes, codes)
    check("C", "no central warehouse in the demo", "MAIN" not in codes, codes)
    equals("C", "exactly two shops", len(warehouses), 2)

    shop1 = qty_at(variant, "SHOP1")
    shop2 = qty_at(variant, "SHOP2")
    print(f"       {SKU}:  Shop 1 = {shop1}   Shop 2 = {shop2}")
    equals("C", "Shop 1 opening stock", shop1, EXPECTED["opening_shop1"])
    equals("C", "Shop 2 opening stock", shop2, EXPECTED["opening_shop2"])
    check("C", "the same variant holds different stock per shop", shop1 != shop2)


# ===========================================================================
# Part D - purchase straight into a shop
# ===========================================================================
def part_d_purchase():
    section("PART D  Supplier -> Shop 1 (no central warehouse)")
    variant = CONTEXT.get("variant")
    if not variant:
        return

    supplier = env["res.partner"].search([("name", "=", "ABC Footwear Factory")], limit=1)
    check("D", "supplier exists", bool(supplier))
    if not supplier:
        return

    vendor_price = env["product.supplierinfo"].search(
        [("partner_id", "=", supplier.id), ("product_tmpl_id", "=", variant.product_tmpl_id.id)],
        limit=1,
    )
    check("D", "vendor price exists", bool(vendor_price))
    if vendor_price:
        equals("D", "vendor price", vendor_price.price, EXPECTED["vendor_price"])

    shop1 = env["stock.warehouse"].search([("code", "=", "SHOP1")], limit=1)
    before = qty_at(variant, "SHOP1")

    order = env["purchase.order"].create(
        {
            "partner_id": supplier.id,
            # This is what sends the goods to Shop 1 rather than anywhere else.
            "picking_type_id": shop1.in_type_id.id,
            "order_line": [
                (0, 0, {
                    "product_id": variant.id,
                    "product_qty": PURCHASE_QTY,
                    "price_unit": EXPECTED["vendor_price"],
                    "name": variant.display_name,
                    "product_uom": variant.uom_id.id,
                })
            ],
        }
    )
    check("D", f"purchase order created for {PURCHASE_QTY} x {SKU}", bool(order), order.name)
    check("D", "purchase is destined for Shop 1",
          order.picking_type_id.warehouse_id == shop1, order.picking_type_id.warehouse_id.code)

    order.button_confirm()
    env.cr.commit()
    check("D", "purchase order is confirmed", order.state == "purchase", order.state)
    equals("D", "confirming the PO does NOT increase stock", qty_at(variant, "SHOP1"), before)

    picking = order.picking_ids[:1]
    check("D", "an incoming receipt was created", bool(picking), picking.name if picking else "")
    if not picking:
        return
    check("D", "the receipt is destined for Shop 1",
          picking.location_dest_id == shop1.lot_stock_id, picking.location_dest_id.complete_name)

    for move in picking.move_ids:
        move.quantity = move.product_uom_qty
        if hasattr(move, "picked"):
            move.picked = True
    picking.button_validate()
    env.cr.commit()

    check("D", "the receipt is validated", picking.state == "done", picking.state)
    after = qty_at(variant, "SHOP1")
    print(f"       Shop 1: {before} -> {after}")
    equals("D", "receipt increased Shop 1 stock", after, before + PURCHASE_QTY)
    equals("D", "Shop 1 is at the expected level", after, EXPECTED["after_receipt_shop1"])
    equals("D", "Shop 2 was NOT touched", qty_at(variant, "SHOP2"), EXPECTED["opening_shop2"])


# ===========================================================================
# Parts E / F - POS sale at a given shop and pricelist
# ===========================================================================
def sell_at(part, pos_name, shop_code, pricelist_key, expected_price):
    variant = CONTEXT.get("variant")
    if not variant:
        return None

    penv = pos_user_env()
    config = penv["pos.config"].search([("name", "=", pos_name)], limit=1)
    check(part, f"{pos_name} is configured", bool(config))
    if not config:
        return None

    warehouse = env["stock.warehouse"].search([("code", "=", shop_code)], limit=1)
    check(part, f"{pos_name} is bound to {shop_code} stock",
          config.picking_type_id.warehouse_id == warehouse,
          config.picking_type_id.warehouse_id.code)

    pricelist = CONTEXT.get(f"pricelist_{pricelist_key}")
    check(part, f"{pricelist_key} pricelist is available at this till",
          pricelist in config.available_pricelist_ids,
          config.available_pricelist_ids.mapped("name"))

    scanned = env["product.product"].search(
        [("barcode", "=", variant.barcode), ("available_in_pos", "=", True)], limit=1
    )
    check(part, "scanning the barcode resolves to the right variant", scanned == variant,
          scanned.display_name)

    session = penv["pos.session"].search(
        [("config_id", "=", config.id), ("state", "in", ("opening_control", "opened"))], limit=1
    )
    if not session:
        config.open_ui()
        session = config.current_session_id
    if session.state == "opening_control":
        session.set_opening_control(0, None)
    check(part, "a POS session is open", session.state == "opened", session.state)

    before = qty_at(variant, shop_code)
    price = pricelist._get_product_price(variant, 1.0)
    equals(part, f"{pricelist_key} price applies", price, expected_price)

    taxes = variant.taxes_id.filtered(lambda tax: tax.company_id == env.company)
    tax_result = taxes.compute_all(price, currency=config.currency_id, quantity=1.0, product=variant)
    subtotal, total = tax_result["total_excluded"], tax_result["total_included"]

    order = penv["pos.order"].create(
        {
            "company_id": env.company.id,
            "session_id": session.id,
            "pricelist_id": pricelist.id,
            "amount_tax": total - subtotal,
            "amount_total": total,
            "amount_paid": 0.0,
            "amount_return": 0.0,
            "lines": [(0, 0, {
                "product_id": variant.id,
                "full_product_name": variant.display_name,
                "qty": 1.0,
                "price_unit": price,
                "discount": 0.0,
                "price_subtotal": subtotal,
                "price_subtotal_incl": total,
                "tax_ids": [(6, 0, taxes.ids)],
            })],
        }
    )
    payment_method = config.payment_method_ids[:1]
    if payment_method:
        penv["pos.payment"].create({
            "pos_order_id": order.id,
            "payment_method_id": payment_method.id,
            "amount": total,
        })
    # amount_paid is a plain stored field, not computed from pos.payment.
    order.amount_paid = total
    order.action_pos_order_paid()
    if hasattr(order, "_create_order_picking"):
        order._create_order_picking()
    env.cr.commit()

    check(part, "the POS order is paid", order.state in ("paid", "done", "invoiced"), order.state)
    after = qty_at(variant, shop_code)
    print(f"       {pos_name}: {total:,.2f} ETB incl. tax | {shop_code} stock {before} -> {after}")
    equals(part, f"{shop_code} stock decreased by exactly one", after, before - 1)

    CONTEXT.setdefault("orders", []).append(order)
    return order


def part_e_retail_sale():
    section("PART E  Retail sale at Shop 1")
    order = sell_at("E", "Shop 1 POS", "SHOP1", "retail", EXPECTED["retail_price"])
    if order:
        equals("E", "Shop 1 is at the expected level",
               qty_at(CONTEXT["variant"], "SHOP1"), EXPECTED["after_sale_shop1"])
        equals("E", "Shop 2 was NOT touched by a Shop 1 sale",
               qty_at(CONTEXT["variant"], "SHOP2"), EXPECTED["opening_shop2"])
        check("E", "the sale is attributable to Shop 1",
              order.config_id.name == "Shop 1 POS", order.config_id.name)
    return order


def part_f_wholesale():
    section("PART F  Wholesale price, same product, no duplication")
    variant = CONTEXT.get("variant")
    if not variant:
        return None
    before_shop2 = qty_at(variant, "SHOP2")
    order = sell_at("F", "Shop 2 POS", "SHOP2", "wholesale", EXPECTED["wholesale_price"])
    if order:
        equals("F", "Shop 2 stock decreased", qty_at(variant, "SHOP2"), before_shop2 - 1)
        check("F", "the sale is attributable to Shop 2",
              order.config_id.name == "Shop 2 POS", order.config_id.name)
        # The decisive check: retail and wholesale sold the SAME product record.
        sold = {line.product_id.id for o in CONTEXT["orders"] for line in o.lines}
        equals("F", "retail and wholesale sold the same product record", len(sold), 1)
    return order


# ===========================================================================
# Part G - fiscalization
# ===========================================================================
def part_g_fiscal(order):
    section("PART G  Fiscal transaction -> gateway -> mock provider -> IRN")
    if not order:
        return None

    transaction = env["et.fiscal.transaction"].search(
        [("source_model", "=", "pos.order"), ("source_record_id", "=", order.id)], limit=1
    )
    check("G", "a fiscal transaction was created for the sale", bool(transaction))
    if not transaction:
        return None

    check("G", "it links back to the POS order", transaction.source_record_id == order.id)
    check("G", "it has a stable idempotency key", bool(transaction.idempotency_key))

    if transaction.state not in ("registered", "failed"):
        transaction.action_submit()
        env.cr.commit()

    print(f"       {transaction.name}: state={transaction.state}")
    check("G", "the request reached the gateway", bool(transaction.gateway_document_id))
    check("G", "the document is registered", transaction.state == "registered", transaction.last_error or "")
    check("G", "an IRN was returned and stored", bool(transaction.irn), transaction.irn)
    check("G", "a QR payload was returned and stored", bool(transaction.qr_payload))
    check("G", "a provider reference was stored", bool(transaction.provider_transaction_id))

    # Duplicate protection.
    original_irn = transaction.irn
    transaction._submit()
    env.cr.commit()
    check("G", "re-submitting returns the same IRN", transaction.irn == original_irn, transaction.irn)
    duplicates = env["et.fiscal.transaction"].search(
        [("source_model", "=", "pos.order"), ("source_record_id", "=", order.id)]
    )
    equals("G", "the sale still has exactly one fiscal transaction", len(duplicates), 1)

    CONTEXT["fiscal_transaction"] = transaction
    return transaction


# ===========================================================================
# Part H - provider architecture
# ===========================================================================
def part_h_providers():
    section("PART H  Provider rails are configuration, not code")

    try:
        health = env["et.fiscal.gateway.client"].health()
    except Exception as exc:  # noqa: BLE001
        check("H", "the gateway is reachable", False, exc)
        return
    providers = health.get("providers", {})
    print(f"       active providers: {providers}")

    check("H", "the gateway is healthy", health.get("status") == "pass", health.get("status"))
    for rail in ("fiscal", "payment", "delivery"):
        check("H", f"{rail} provider is selected by configuration",
              providers.get(rail) == "mock", providers.get(rail))

    check("H", "an Integration Gateway payment provider exists",
          bool(env["payment.provider"].search([("code", "=", "integration_gateway")], limit=1)))
    check("H", "an Integration Gateway delivery carrier exists",
          bool(env["delivery.carrier"].search([("delivery_type", "=", "integration_gateway")], limit=1)))

    # The status screen an operator actually uses.
    status = env["mati.integration.status"]._collect()
    check("H", "the integration status screen reports the live providers",
          status.get("fiscal_provider") == "mock", status.get("fiscal_provider"))
    check("H", "the status screen exposes no secrets",
          not any("key" in str(k).lower() or "secret" in str(k).lower() for k in status))


# ===========================================================================
# Part I - failure and recovery
# ===========================================================================
def part_i_recovery():
    section("PART I  Provider outage, visible failure, retry, one registration")

    try:
        gateway_admin("/api/v1/admin/mock/fiscal/failure-mode", {"enabled": True})
        check("I", "the mock fiscal provider was forced into failure mode", True)
    except Exception as exc:  # noqa: BLE001
        check("I", "the mock fiscal provider was forced into failure mode", False, exc)
        return

    variant = CONTEXT.get("variant")
    before = qty_at(variant, "SHOP1") if variant else None
    order = sell_at("I", "Shop 1 POS", "SHOP1", "retail", EXPECTED["retail_price"])
    if not order:
        gateway_admin("/api/v1/admin/mock/fiscal/failure-mode", {"enabled": False})
        return

    transaction = env["et.fiscal.transaction"].search(
        [("source_model", "=", "pos.order"), ("source_record_id", "=", order.id)], limit=1
    )
    check("I", "a fiscal transaction exists despite the outage", bool(transaction))
    if not transaction:
        gateway_admin("/api/v1/admin/mock/fiscal/failure-mode", {"enabled": False})
        return

    if transaction.state not in ("failed", "registered"):
        transaction.action_submit()
        env.cr.commit()

    check("I", "the fiscal state is visibly failed", transaction.state == "failed", transaction.state)
    check("I", "the failure reason is visible", bool(transaction.last_error), transaction.last_error)
    check("I", "the document is still retryable", transaction.is_retryable)
    check("I", "the sale completed normally", order.state in ("paid", "done", "invoiced"), order.state)
    if before is not None:
        equals("I", "stock still decremented during the outage", qty_at(variant, "SHOP1"), before - 1)

    failed_key = transaction.idempotency_key

    gateway_admin("/api/v1/admin/mock/fiscal/failure-mode", {"enabled": False})
    check("I", "the provider was restored", True)

    transaction.action_retry()
    env.cr.commit()
    check("I", "the retry registered the document", transaction.state == "registered",
          transaction.last_error or "")
    check("I", "an IRN was assigned on retry", bool(transaction.irn), transaction.irn)
    check("I", "the idempotency key never changed", transaction.idempotency_key == failed_key)

    irn = transaction.irn
    transaction._submit()
    env.cr.commit()
    check("I", "retrying again yields the same IRN", transaction.irn == irn, transaction.irn)

    all_for_order = env["et.fiscal.transaction"].search(
        [("source_model", "=", "pos.order"), ("source_record_id", "=", order.id)]
    )
    equals("I", "exactly one fiscal registration exists for this sale", len(all_for_order), 1)

    if transaction.gateway_document_id:
        try:
            audit = gateway_admin(
                f"/api/v1/admin/audit/fiscal_document/{transaction.gateway_document_id}"
            )
            outcomes = [attempt["outcome"] for attempt in audit["attempts"]]
            print(f"       gateway attempts: {outcomes}")
            check("I", "failed attempts are preserved in the audit trail",
                  "transient_error" in outcomes, outcomes)
            check("I", "the successful attempt is recorded", "success" in outcomes, outcomes)
        except Exception as exc:  # noqa: BLE001
            check("I", "the gateway audit trail is readable", False, exc)


# ===========================================================================
# Invariant - inventory is explained by stock moves
# ===========================================================================
def invariant_reconciliation():
    section("INVARIANT  Shop inventory is fully explained by stock moves")
    variant = CONTEXT.get("variant")
    if not variant:
        return

    for code in ("SHOP1", "SHOP2"):
        warehouse = env["stock.warehouse"].search([("code", "=", code)], limit=1)
        if not warehouse:
            continue
        location = warehouse.lot_stock_id
        on_hand = env["stock.quant"]._get_available_quantity(variant, location)

        # A move wholly inside this location appears in both sums and cancels.
        incoming = sum(env["stock.move"].search([
            ("product_id", "=", variant.id), ("state", "=", "done"),
            ("location_dest_id", "child_of", location.id),
        ]).mapped("quantity"))
        outgoing = sum(env["stock.move"].search([
            ("product_id", "=", variant.id), ("state", "=", "done"),
            ("location_id", "child_of", location.id),
        ]).mapped("quantity"))
        computed = incoming - outgoing
        print(f"       {code}: in {incoming} - out {outgoing} = {computed}  (on hand {on_hand})")
        equals("invariant", f"{code} on-hand equals inbound minus outbound moves", on_hand, computed)


# ===========================================================================
# Main
# ===========================================================================
def main():
    print("")
    print("#" * 78)
    print("#  MATI'S SHOES - END TO END VERIFICATION")
    print("#  DEMO FISCAL REGISTRATION - NOT PRODUCTION CERTIFICATION")
    print("#" * 78)

    try:
        part_a_product()
        part_b_pricing()
        part_c_shop_stock()
        part_d_purchase()
        retail_order = part_e_retail_sale()
        part_f_wholesale()
        part_g_fiscal(retail_order)
        part_h_providers()
        part_i_recovery()
        invariant_reconciliation()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        RESULTS.append({
            "step": "fatal", "description": "verification crashed",
            "status": "FAIL", "detail": traceback.format_exc()[-2000:],
        })

    passed = sum(1 for r in RESULTS if r["status"] == "PASS")
    failed = sum(1 for r in RESULTS if r["status"] == "FAIL")

    section("SUMMARY")
    if failed:
        print("Failed checks:")
        for result in RESULTS:
            if result["status"] == "FAIL":
                print(f"  - [{result['step']}] {result['description']}: {result['detail']}")
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
