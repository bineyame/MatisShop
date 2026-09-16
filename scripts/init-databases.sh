#!/bin/bash
# Runs once, on first PostgreSQL container initialisation.
#
# Creates the gateway's OWN logical database. Odoo creates its business database
# itself during bootstrap. The two schemas are never mixed (ADR-006).
set -euo pipefail

GATEWAY_DB="${GATEWAY_DB_NAME:-integration_gateway}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-SQL
    SELECT 'CREATE DATABASE $GATEWAY_DB'
     WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$GATEWAY_DB')\gexec
SQL

echo "[init-databases] ensured database '$GATEWAY_DB' exists"
