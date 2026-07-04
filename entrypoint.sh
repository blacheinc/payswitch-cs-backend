#!/bin/sh
set -eu

# ── Database migrations ──────────────────────────────────────────────────────
# Fatal in production so a broken deploy doesn't silently run with stale schema.
# Non-fatal outside production for faster local iteration.
echo "Running database migrations..."
if [ "${RUN_DB_MIGRATIONS:-true}" != "true" ]; then
    echo "Database migrations disabled for this workload."
elif [ "${APP_ENV:-development}" = "production" ]; then
    alembic upgrade head
    echo "Migrations completed."
else
    if ! timeout 30 alembic upgrade head; then
        echo "WARNING: migrations failed or timed out — continuing (non-production)."
    fi
fi

# ── Bootstrap admin (only runs when BOOTSTRAP_ADMIN_* are set) ───────────────
echo "Checking for bootstrap admin..."
if [ "${RUN_BOOTSTRAP_ADMIN:-true}" != "true" ]; then
    echo "Bootstrap admin disabled for this workload."
elif ! python scripts/create_superuser.py; then
    echo "WARNING: bootstrap admin step failed — continuing."
fi

# ── Launch the app ───────────────────────────────────────────────────────────
exec "$@"
