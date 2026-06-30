#!/bin/sh
set -eu

PYTHON="${PYTHON:-.venv/bin/python}"

"$PYTHON" -m ruff check app tests scripts
"$PYTHON" -m ruff format --check app tests scripts
"$PYTHON" -m mypy app tests
"$PYTHON" -m pytest --cov=app --cov-report=term
"$PYTHON" -m pytest \
    --cov=app.core.security \
    --cov=app.dependencies \
    --cov=app.services.hard_stops \
    --cov=app.services.scoring_pipeline \
    --cov=app.subscribers.batch_complete \
    --cov-fail-under=80 \
    --cov-report=term
"$PYTHON" -m pip_audit
"$PYTHON" -m bandit -r app -q

kubectl kustomize infra/k8s/base >/tmp/payswitch-k8s-base.yaml
kubectl kustomize infra/k8s/overlays/example >/tmp/payswitch-k8s-overlay.yaml
grep -q 'argocd.argoproj.io/hook: PreSync' /tmp/payswitch-k8s-overlay.yaml
grep -q 'runAsNonRoot: true' /tmp/payswitch-k8s-overlay.yaml
docker compose -f docker-compose.integration.yml config --quiet

echo "PaySwitch handover verification passed."
