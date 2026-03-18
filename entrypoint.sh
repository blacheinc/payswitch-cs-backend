#!/bin/sh

# Exit immediately if a command exits with a non-zero status
set -e

# Run database migrations
echo "Running database migrations..."
alembic upgrade head

# Create/Verify default admin
echo "Ensuring default admin user..."
python scripts/create_superuser.py

# Exec the container's main process (what's set as CMD)
exec "$@"
