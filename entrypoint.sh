#!/bin/sh

# Run database migrations (non-fatal, 15s timeout — app should still start)
echo "Running database migrations..."
if timeout 15 alembic upgrade head; then
    echo "Migrations completed successfully."
else
    echo "WARNING: Migrations failed or timed out. App will start anyway — check DATABASE_URL."
fi

# Create/Verify default admin (non-fatal, 15s timeout)
echo "Ensuring default admin user..."
if timeout 15 python scripts/create_superuser.py; then
    echo "Superuser check completed."
else
    echo "WARNING: Superuser script failed or timed out. Continuing..."
fi

# Exec the container's main process (what's set as CMD)
exec "$@"
