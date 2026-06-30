#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/payswitch-e2e.XXXXXX")
PG_DATA="$WORK_DIR/postgres"
PG_LOG="$WORK_DIR/postgres.log"
API_LOG="$WORK_DIR/api.log"
PG_PORT="${E2E_POSTGRES_PORT:-55432}"
API_PORT="${E2E_API_PORT:-58000}"
API_PID=""

cleanup() {
    if [ -n "$API_PID" ]; then
        kill "$API_PID" 2>/dev/null || true
        wait "$API_PID" 2>/dev/null || true
    fi
    pg_ctl -D "$PG_DATA" -m fast stop >/dev/null 2>&1 || true
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT INT TERM

cd "$ROOT_DIR"
initdb -D "$PG_DATA" -A trust -U payswitch >/dev/null
pg_ctl -D "$PG_DATA" -l "$PG_LOG" -o "-h 127.0.0.1 -p $PG_PORT" start >/dev/null
createdb -h 127.0.0.1 -p "$PG_PORT" -U payswitch payswitch_integration

export APP_ENV=development
export DEBUG=false
export DATABASE_URL="postgresql+asyncpg://payswitch@127.0.0.1:${PG_PORT}/payswitch_integration"
export REDIS_URL=memory://
export JWT_SECRET_KEY=integration-only-jwt-secret-at-least-32-characters
export EMAIL_PROVIDER=console
export DEMO_SIMULATION_MODE=true
export DEMO_BUREAU_DELAY_SECONDS=0
export DEMO_SCORING_DELAY_SECONDS=0
export AZURE_SERVICEBUS_CONNECTION_STRING=
export AZURE_STORAGE_CONNECTION_STRING=

.venv/bin/alembic upgrade head
.venv/bin/python scripts/seed_demo_data.py --reset --scale small --minimal

.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" >"$API_LOG" 2>&1 &
API_PID=$!

ready=false
for _ in $(seq 1 30); do
    if curl --fail --silent "http://127.0.0.1:${API_PORT}/ready" >/dev/null; then
        ready=true
        break
    fi
    sleep 1
done
if [ "$ready" != "true" ]; then
    cat "$API_LOG"
    exit 1
fi

TEST_API_BASE_URL="http://127.0.0.1:${API_PORT}" \
TEST_ORG_EMAIL=admin@gcm.com.gh \
TEST_ORG_PASSWORD='DemoPass!2026' \
    .venv/bin/python scripts/test_batch_score_flow.py

curl --fail --silent "http://127.0.0.1:${API_PORT}/health" >/dev/null
curl --fail --silent "http://127.0.0.1:${API_PORT}/ready" >/dev/null

echo "Local handover E2E passed: health, readiness, auth, and 3-item batch flow."
