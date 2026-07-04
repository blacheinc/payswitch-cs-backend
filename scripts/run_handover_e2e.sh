#!/bin/sh
set -eu

COMPOSE_FILE="docker-compose.integration.yml"

cleanup() {
    docker compose -f "$COMPOSE_FILE" down -v --remove-orphans
}
trap cleanup EXIT INT TERM

docker compose -f "$COMPOSE_FILE" down -v --remove-orphans
docker compose -f "$COMPOSE_FILE" up -d --build --wait

docker compose -f "$COMPOSE_FILE" exec -T api \
    python scripts/seed_demo_data.py --reset --scale small --minimal

TEST_API_BASE_URL=http://localhost:58000 \
TEST_ORG_EMAIL=admin@gcm.com.gh \
TEST_ORG_PASSWORD='DemoPass!2026' \
    .venv/bin/python scripts/test_batch_score_flow.py

curl --fail --silent http://localhost:58000/health >/dev/null
curl --fail --silent http://localhost:58000/ready >/dev/null

echo "Handover E2E passed: isolated health, readiness, authentication, and 3-item batch flow."
