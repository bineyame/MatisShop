#!/usr/bin/env bash
# Re-run the demo seeding. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="${COMPOSE:-docker compose}"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
ODOO_DB="${ODOO_DB_NAME:-odoo}"

echo "==> Seeding demo data in '${ODOO_DB}'"
$COMPOSE exec -T odoo odoo shell -d "${ODOO_DB}" --no-http --log-level=warn <<'PYEOF'
env["mati.demo.setup"].seed_all()
env.cr.commit()
snapshot = env["mati.demo.setup"].demo_snapshot()
print("")
print("Seeded stock:")
for sku, locations in sorted(snapshot["stock"].items()):
    print(f"  {sku:<12} " + "  ".join(f"{code}={int(qty)}" for code, qty in sorted(locations.items())))
print("")
print("Seeded prices for SAM-BLK-42:")
for context, price in snapshot["prices"].items():
    print(f"  {context:<16} {price:,.2f} ETB")
print("")
print("SEED RESULT: OK")
PYEOF
echo "==> Done"
