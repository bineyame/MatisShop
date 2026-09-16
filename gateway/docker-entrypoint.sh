#!/bin/sh
# Applies database migrations, then starts the API.
set -eu

case "${1:-serve}" in
  serve)
    echo "[gateway] applying migrations..."
    alembic upgrade head
    echo "[gateway] starting uvicorn on 0.0.0.0:8000"
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-config /dev/null
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  test)
    shift
    exec pytest "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
