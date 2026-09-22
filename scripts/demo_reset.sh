#!/usr/bin/env bash
# =============================================================================
# Restore a clean, rehearsable demo state.
#
#   1. re-seed  (idempotent: products, prices, shops, POS, supplier)
#   2. reset    (close sessions, cancel demo POs/transfers, restore stock)
#   3. restore  the mock providers to working mode
#
# Fiscal transactions are deliberately KEPT: deleting a registration history
# is exactly what a fiscal system must never do. They are visibly historical.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="${COMPOSE:-docker compose}"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
ODOO_DB="${ODOO_DB_NAME:-odoo}"

echo "==> Restoring demo state in '${ODOO_DB}'"

# NB: `docker compose exec` bypasses the image ENTRYPOINT, so we invoke it
# explicitly. Without it Odoo gets no --db_host/--db_user and tries a local
# unix socket.
$COMPOSE exec -T odoo mati-entrypoint.sh odoo shell -d "${ODOO_DB}" --no-http --log-level=warn <<'PYEOF'
setup = env["mati.demo.setup"]

setup.seed_all()
env.cr.commit()

setup.action_reset_demo()
env.cr.commit()

snapshot = setup.demo_snapshot()
print("")
print("Stock restored to:")
print(f"  {'SKU':<20} " + "  ".join(f"{c:>7}" for c in ("SHOP1", "SHOP2")))
for sku, shops in sorted(snapshot["stock"].items()):
    print(f"  {sku:<20} " + "  ".join(f"{int(shops.get(c, 0)):>7}" for c in ("SHOP1", "SHOP2")))
print("")
print(f"Prices for {snapshot['sku']}:")
for context, price in snapshot["prices"].items():
    print(f"  {context:<12} {price:,.2f} ETB")
print("")
print("DEMO RESET: OK")
PYEOF

echo "==> Demo ready. Walk it with docs/demo-script.md"
