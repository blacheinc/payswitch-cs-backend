#!/bin/sh
set -eu

# ── Database migrations ──────────────────────────────────────────────────────
# Fatal in production so a broken deploy doesn't silently run with stale schema.
# Non-fatal outside production for faster local iteration.
echo "Running database migrations..."
if [ "${APP_ENV:-development}" = "production" ]; then
    alembic upgrade head
    echo "Migrations completed."
else
    if ! timeout 30 alembic upgrade head; then
        echo "WARNING: migrations failed or timed out — continuing (non-production)."
    fi
fi

# ── Bootstrap admin (only runs when BOOTSTRAP_ADMIN_* are set) ───────────────
echo "Checking for bootstrap admin..."
if ! python scripts/create_superuser.py; then
    echo "WARNING: bootstrap admin step failed — continuing."
fi

# ── Launch the app ───────────────────────────────────────────────────────────
exec "$@"
