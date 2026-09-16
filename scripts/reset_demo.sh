#!/usr/bin/env bash
# Restore the seeded opening state.
#
# Cancels demo purchase orders and open transfers, then re-applies the opening
# inventory. Fiscal transactions are deliberately KEPT: deleting a registration
# history is exactly what a fiscal system must never do.
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="${COMPOSE:-docker compose}"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
ODOO_DB="${ODOO_DB_NAME:-odoo}"

echo "==> Resetting demo state in '${ODOO_DB}'"
$COMPOSE exec -T odoo odoo shell -d "${ODOO_DB}" --no-http --log-level=warn <<'PYEOF'
# Close any open POS session so the next demo starts clean.
for session in env["pos.session"].search([("state", "!=", "closed")]):
    try:
        session.action_pos_session_closing_control()
        session.action_pos_session_close()
    except Exception as exc:
        print(f"  could not close session {session.name}: {exc}")

env["mati.demo.setup"].action_reset_demo()
env.cr.commit()

snapshot = env["mati.demo.setup"].demo_snapshot()
print("")
print("Stock restored to:")
for sku, locations in sorted(snapshot["stock"].items()):
    print(f"  {sku:<12} " + "  ".join(f"{code}={int(qty)}" for code, qty in sorted(locations.items())))
print("")
print("RESET RESULT: OK")
PYEOF

echo "==> Restoring the mock fiscal provider to working mode"
curl -fsS -X POST "http://localhost:${GATEWAY_PORT:-8000}/api/v1/admin/mock/fiscal/failure-mode" \
  -H "X-API-Key: ${GATEWAY_API_KEY:-dev-gateway-api-key-change-me}" \
  -H "Content-Type: application/json" -d '{"enabled": false}' >/dev/null || true
echo "==> Done"
