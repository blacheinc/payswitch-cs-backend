#!/bin/sh

# Run database migrations (non-fatal — app should still start)
echo "Running database migrations..."
if alembic upgrade head; then
    echo "Migrations completed successfully."
else
    echo "WARNING: Migrations failed. App will start anyway — check DATABASE_URL."
fi

# Create/Verify default admin (non-fatal)
echo "Ensuring default admin user..."
if python scripts/create_superuser.py; then
    echo "Superuser check completed."
else
    echo "WARNING: Superuser script failed. Continuing..."
fi

# Exec the container's main process (what's set as CMD)
exec "$@"
