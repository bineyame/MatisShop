#!/usr/bin/env bash
# Run the end-to-end verification inside Odoo and report a real exit code.
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="${COMPOSE:-docker compose}"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
ODOO_DB="${ODOO_DB_NAME:-odoo}"

OUTPUT=$(mktemp)
trap 'rm -f "$OUTPUT"' EXIT

echo "==> Running end-to-end verification against database '${ODOO_DB}'"
$COMPOSE exec -T odoo odoo shell -d "${ODOO_DB}" --no-http --log-level=warn \
  < tools/verify_demo.py 2>&1 | tee "$OUTPUT"

if grep -q "VERIFY RESULT: PASS" "$OUTPUT"; then
  echo ""
  echo "VERIFICATION PASSED"
  exit 0
fi

echo ""
echo "VERIFICATION FAILED"
echo "Full report: docker compose exec odoo cat /tmp/verify_report.json"
exit 1
