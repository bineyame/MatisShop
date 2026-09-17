#!/bin/bash
# Entrypoint for the pinned-source Odoo container.
#   `odoo`                  -> start the HTTP server
#   `odoo shell -d odoo`    -> any odoo-bin subcommand
#   `odoo -d odoo -i mod`   -> server options
#   anything else           -> executed verbatim
#
# Note for `docker compose exec`: exec bypasses ENTRYPOINT, so call this script
# explicitly (`docker compose exec odoo mati-entrypoint.sh odoo shell ...`) or
# Odoo receives no database connection parameters.
set -euo pipefail

ODOO_SOURCE="${ODOO_SOURCE:-/opt/odoo/source}"
DB_HOST="${HOST:-postgres}"
DB_PORT="${PORT:-5432}"
DB_USER="${USER:-odoo}"
DB_PASSWORD="${PASSWORD:-odoo}"

export PGHOST="$DB_HOST" PGPORT="$DB_PORT" PGUSER="$DB_USER" PGPASSWORD="$DB_PASSWORD"

wait_for_postgres() {
  local retries=60
  until psql -d postgres -c 'SELECT 1' >/dev/null 2>&1; do
    retries=$((retries - 1))
    if [ "$retries" -le 0 ]; then
      echo "[entrypoint] postgres did not become available in time" >&2
      exit 1
    fi
    sleep 1
  done
}

if [ "${1:-}" = "odoo" ]; then
  shift
  wait_for_postgres
  echo "[entrypoint] Odoo source revision: $(cat /opt/odoo/REVISION 2>/dev/null || echo unknown)"

  # odoo-bin takes its subcommand as the FIRST argument; options must come
  # after it. Passing `-c ... shell` makes odoo-bin default to `server` and
  # then reject `shell` as an unrecognized parameter.
  SUBCOMMAND=""
  case "${1:-}" in
    shell|scaffold|populate|cloc|db|deploy|neutralize|obfuscate|start|tsconfig|upgrade_code|genproxytoken)
      SUBCOMMAND="$1"
      shift
      ;;
  esac

  exec python3 "${ODOO_SOURCE}/odoo-bin" ${SUBCOMMAND} \
    -c /etc/odoo/odoo.conf \
    --db_host="$DB_HOST" --db_port="$DB_PORT" \
    --db_user="$DB_USER" --db_password="$DB_PASSWORD" \
    "$@"
fi

exec "$@"
