#!/usr/bin/env bash
# Check every service.
set -uo pipefail
cd "$(dirname "$0")/.."

COMPOSE="${COMPOSE:-docker compose}"
if [ -f .env ]; then set -a; . ./.env; set +a; fi

status=0

printf 'postgres  ... '
if $COMPOSE exec -T postgres pg_isready -U "${POSTGRES_USER:-odoo}" -d postgres >/dev/null 2>&1; then
  echo "ok"
else
  echo "FAIL"; status=1
fi

printf 'odoo      ... '
if curl -fsS "http://localhost:${ODOO_PORT:-8069}/web/health" >/dev/null 2>&1; then
  echo "ok  (http://localhost:${ODOO_PORT:-8069})"
else
  echo "FAIL"; status=1
fi

printf 'gateway   ... '
GATEWAY_HEALTH=$(curl -fsS "http://localhost:${GATEWAY_PORT:-8000}/health" 2>/dev/null)
if [ -n "$GATEWAY_HEALTH" ]; then
  echo "ok  (http://localhost:${GATEWAY_PORT:-8000})"
  echo "$GATEWAY_HEALTH" | python -m json.tool 2>/dev/null | sed 's/^/            /' || echo "            $GATEWAY_HEALTH"
else
  echo "FAIL"; status=1
fi

exit $status
