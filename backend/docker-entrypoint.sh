#!/usr/bin/env sh
# Container entrypoint: optionally seed checks for a Supabase user, then serve.
# Users are managed by Supabase Auth (sign up in the web UI), so there is no
# admin user to create here.
set -e

if [ -n "$SEED_CHECKS" ] && [ -n "$SEED_USER_ID" ]; then
  echo "[entrypoint] seeding checks from $SEED_CHECKS for user $SEED_USER_ID"
  python -m server.seed --checks "$SEED_CHECKS" --user-id "$SEED_USER_ID" \
    || echo "[entrypoint] seed skipped/failed (continuing)"
fi

exec uvicorn server.main:app --host 0.0.0.0 --port "${PORT:-8000}"
