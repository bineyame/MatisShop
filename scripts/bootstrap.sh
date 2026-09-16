#!/usr/bin/env bash
# =============================================================================
# Create the Odoo demo database and install every module.
#
# Idempotent: running it on an existing database updates the modules instead of
# recreating it. Use `make nuke` first for a genuinely clean rebuild.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE="${COMPOSE:-docker compose}"
ODOO_DB="${ODOO_DB_NAME:-odoo}"
MODULES="et_fiscal_odoo,external_payment_gateway,external_delivery_gateway,mati_demo"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi
ODOO_DB="${ODOO_DB_NAME:-odoo}"

info()  { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }
ok()    { printf '\033[0;32m    %s\033[0m\n' "$1"; }
warn()  { printf '\033[0;33m    %s\033[0m\n' "$1"; }

info "Waiting for PostgreSQL"
for _ in $(seq 1 60); do
  if $COMPOSE exec -T postgres pg_isready -U "${POSTGRES_USER:-odoo}" -d postgres >/dev/null 2>&1; then
    ok "PostgreSQL is ready"
    break
  fi
  sleep 2
done

info "Waiting for the Integration Gateway"
for _ in $(seq 1 60); do
  if curl -fsS http://localhost:"${GATEWAY_PORT:-8000}"/health >/dev/null 2>&1; then
    ok "Gateway is ready"
    break
  fi
  sleep 2
done

db_exists() {
  $COMPOSE exec -T postgres psql -U "${POSTGRES_USER:-odoo}" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='${ODOO_DB}'" 2>/dev/null | grep -q 1
}

if db_exists; then
  info "Database '${ODOO_DB}' already exists - updating modules"
  $COMPOSE stop odoo >/dev/null 2>&1 || true
  $COMPOSE run --rm odoo odoo -d "${ODOO_DB}" -u "${MODULES}" --stop-after-init --log-level=warn
  $COMPOSE start odoo
else
  info "Creating database '${ODOO_DB}' and installing modules"
  warn "This takes a few minutes on a first run (Odoo builds all assets)."
  $COMPOSE stop odoo >/dev/null 2>&1 || true
  # `-i base` with `--without-demo=all` gives a clean database; mati_demo then
  # seeds the deterministic demo dataset through its post_init hook.
  $COMPOSE run --rm odoo odoo -d "${ODOO_DB}" -i "base,${MODULES}" \
    --without-demo=all --stop-after-init --log-level=warn
  $COMPOSE start odoo
fi

info "Waiting for Odoo to come back up"
for _ in $(seq 1 90); do
  if curl -fsS http://localhost:"${ODOO_PORT:-8069}"/web/health >/dev/null 2>&1; then
    ok "Odoo is ready"
    break
  fi
  sleep 2
done

cat <<BANNER

  ------------------------------------------------------------------
   Bootstrap complete.

   Odoo      http://localhost:${ODOO_PORT:-8069}
             login    ${ODOO_DEMO_LOGIN:-admin}
             password ${ODOO_DEMO_PASSWORD:-admin}

   Gateway   http://localhost:${GATEWAY_PORT:-8000}
             API docs http://localhost:${GATEWAY_PORT:-8000}/docs

   Next:     make verify      run the end-to-end verification
             docs/demo-script.md   the business walkthrough

   Fiscal registration uses a MOCK provider.
   DEMO FISCAL REGISTRATION - NOT PRODUCTION CERTIFICATION.
  ------------------------------------------------------------------

BANNER
