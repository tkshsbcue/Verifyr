#!/usr/bin/env sh
# Container entrypoint: optionally seed an admin user (and checks), then serve.
set -e

if [ -n "$ADMIN_EMAIL" ] && [ -n "$ADMIN_PASSWORD" ]; then
  echo "[entrypoint] seeding admin user: $ADMIN_EMAIL"
  python -m server.seed --email "$ADMIN_EMAIL" --password "$ADMIN_PASSWORD" \
    ${SEED_CHECKS:+--checks "$SEED_CHECKS"} || echo "[entrypoint] seed skipped/failed (continuing)"
fi

exec uvicorn server.main:app --host 0.0.0.0 --port "${PORT:-8000}"
