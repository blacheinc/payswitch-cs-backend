#!/usr/bin/env bash
# ==============================================================================
# deploy.sh — PaySwitch Credit Scoring — one-shot Azure deploy
#
# What it does (in order):
#   1. Preflight checks: az CLI, Bicep extension, logged-in session,
#      parameters file present.
#   2. Compiles + validates the Bicep template.
#   3. Runs ``az deployment sub what-if`` so the operator can review the
#      delta (skipped with --yes).
#   4. Deploys the Bicep template via ``az deployment sub create``.
#   5. Reads deployment outputs (ACR, Container App, etc.).
#   6. Builds and pushes the API image directly in ACR
#      (``az acr build``) — no local Docker daemon required.
#   7. Swaps the new image onto the Container App, creating a new
#      revision. Waits for the revision to become Provisioned + Healthy.
#   8. Smoke-tests ``GET /health`` on the public FQDN.
#
# Flags:
#   --skip-infra     Skip steps 2-5 (fast path for code-only redeploys).
#   --skip-build     Skip steps 6-7 (infra-only deploy).
#   --yes            Skip the what-if confirmation prompt.
#   --parameters F   Override the default parameters file path.
#   --image-tag T    Image tag to apply (default: git short SHA + timestamp).
#
# Prerequisites (one-time, per environment):
#   * Operator has ``Owner`` on the target subscription (needed to
#     assign Key Vault RBAC roles during the Bicep run).
#   * A service principal exists for the app's Key Vault access; its
#     objectId / appId / secret are populated in the parameters file.
#   * ``az login`` completed; ``az account set --subscription <id>`` run.
#
# Exit codes:
#   0  — success
#   1  — preflight failure
#   2  — Bicep validation failure
#   3  — infra deployment failure
#   4  — image build failure
#   5  — container app update failure
#   6  — smoke test failure
# ==============================================================================
set -euo pipefail

# ── Paths ────────────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BICEP_FILE="$REPO_ROOT/infra/bicep/main.bicep"
PARAMS_FILE="$REPO_ROOT/infra/bicep/main.parameters.json"
DEPLOYMENT_NAME="payswitch-$(date +%Y%m%d-%H%M%S)"

# ── Flags ────────────────────────────────────────────────────────────────────
SKIP_INFRA=0
SKIP_BUILD=0
ASSUME_YES=0
IMAGE_TAG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-infra)  SKIP_INFRA=1; shift ;;
    --skip-build)  SKIP_BUILD=1; shift ;;
    --yes|-y)      ASSUME_YES=1; shift ;;
    --parameters)  PARAMS_FILE="$2"; shift 2 ;;
    --image-tag)   IMAGE_TAG="$2"; shift 2 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown flag: $1" >&2; exit 1 ;;
  esac
done

# ── Pretty output ────────────────────────────────────────────────────────────
RED=$(printf '\033[0;31m')
GRN=$(printf '\033[0;32m')
YEL=$(printf '\033[0;33m')
CYN=$(printf '\033[0;36m')
BLD=$(printf '\033[1m')
RST=$(printf '\033[0m')

_info()  { echo "${CYN}${BLD}[info]${RST} $*"; }
_ok()    { echo "${GRN}${BLD}[ok]  ${RST} $*"; }
_warn()  { echo "${YEL}${BLD}[warn]${RST} $*"; }
_fail()  { echo "${RED}${BLD}[fail]${RST} $*" >&2; }
_step()  { echo; echo "${BLD}══ $* ══${RST}"; }

# ==============================================================================
# 1. Preflight
# ==============================================================================
_step "Preflight"

if ! command -v az >/dev/null 2>&1; then
  _fail "Azure CLI (az) not found — install from https://aka.ms/installazurecliwindows or brew install azure-cli"
  exit 1
fi
_ok "az CLI: $(az version --query '\"azure-cli\"' -o tsv 2>/dev/null || echo 'present')"

if ! az bicep version >/dev/null 2>&1; then
  _info "Installing Bicep CLI extension..."
  az bicep install >/dev/null
fi
_ok "Bicep: $(az bicep version 2>/dev/null | head -1 || echo 'installed')"

SUB_INFO="$(az account show --query '{name:name, id:id, tenant:tenantId}' -o json 2>/dev/null || true)"
if [[ -z "$SUB_INFO" || "$SUB_INFO" == "null" ]]; then
  _fail "Not logged in — run: az login && az account set --subscription <sub-id>"
  exit 1
fi
_ok "Subscription: $(echo "$SUB_INFO" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(f\"{d[\"name\"]} ({d[\"id\"]})\")')"

if [[ ! -f "$PARAMS_FILE" ]]; then
  _fail "Parameters file not found: $PARAMS_FILE"
  exit 1
fi
_ok "Parameters: $PARAMS_FILE"

# Pull resourceGroupName / location / namePrefix out of the params file so
# we can use them for az commands that target the RG directly.
LOCATION="$(python3 -c 'import json,sys; p=json.load(open(sys.argv[1]))["parameters"]; print(p["location"]["value"])' "$PARAMS_FILE")"
RG_NAME="$(python3 -c 'import json,sys; p=json.load(open(sys.argv[1]))["parameters"]; print(p["resourceGroupName"]["value"])' "$PARAMS_FILE")"
_ok "Target: rg=${RG_NAME} region=${LOCATION}"

# ==============================================================================
# 2-5. Infrastructure provisioning (skippable)
# ==============================================================================
if [[ $SKIP_INFRA -eq 1 ]]; then
  _info "--skip-infra: reusing existing deployment outputs"
else
  _step "Validate Bicep"
  if ! az deployment sub validate \
       --location "$LOCATION" \
       --template-file "$BICEP_FILE" \
       --parameters "@$PARAMS_FILE" \
       --output none 2> /tmp/payswitch-bicep-validate.log; then
    _fail "Bicep validation failed — details above"
    cat /tmp/payswitch-bicep-validate.log >&2
    exit 2
  fi
  _ok "Template valid"

  if [[ $ASSUME_YES -eq 0 ]]; then
    _step "What-if preview"
    az deployment sub what-if \
      --location "$LOCATION" \
      --template-file "$BICEP_FILE" \
      --parameters "@$PARAMS_FILE"

    echo
    read -r -p "Proceed with deployment? [y/N] " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
      _warn "Aborted by operator"
      exit 0
    fi
  fi

  _step "Deploy infrastructure"
  _info "Name: $DEPLOYMENT_NAME (this takes 10-15 minutes)"
  if ! az deployment sub create \
       --name "$DEPLOYMENT_NAME" \
       --location "$LOCATION" \
       --template-file "$BICEP_FILE" \
       --parameters "@$PARAMS_FILE" \
       --output none; then
    _fail "Bicep deployment failed — check portal for details"
    exit 3
  fi
  _ok "Infrastructure provisioned"
fi

# ── Capture outputs (needed regardless of --skip-infra) ─────────────────────
_step "Read deployment outputs"

# If we skipped infra, use the latest successful deployment on this RG.
if [[ $SKIP_INFRA -eq 1 ]]; then
  DEPLOYMENT_NAME="$(az deployment sub list \
    --query "[?properties.provisioningState=='Succeeded' && contains(name, 'payswitch')] | sort_by(@, &properties.timestamp) | [-1].name" \
    -o tsv)"
  if [[ -z "$DEPLOYMENT_NAME" ]]; then
    _fail "No prior successful deployment found — re-run without --skip-infra"
    exit 1
  fi
  _info "Reusing deployment: $DEPLOYMENT_NAME"
fi

OUTPUTS="$(az deployment sub show --name "$DEPLOYMENT_NAME" --query properties.outputs -o json)"
ACR_LOGIN="$(echo "$OUTPUTS" | python3 -c 'import sys,json; print(json.load(sys.stdin)["acrLoginServer"]["value"])')"
ACR_NAME="$(echo "$OUTPUTS"  | python3 -c 'import sys,json; print(json.load(sys.stdin)["acrName"]["value"])')"
ACA_APP="$(echo "$OUTPUTS"   | python3 -c 'import sys,json; print(json.load(sys.stdin)["acaAppName"]["value"])')"
ACA_FQDN="$(echo "$OUTPUTS"  | python3 -c 'import sys,json; print(json.load(sys.stdin)["acaAppFqdn"]["value"])')"
_ok "ACR:      $ACR_LOGIN"
_ok "ACA app:  $ACA_APP ($ACA_FQDN)"

# ==============================================================================
# 6-7. Build image in ACR + swap onto Container App
# ==============================================================================
if [[ $SKIP_BUILD -eq 1 ]]; then
  _info "--skip-build: leaving Container App on its current image"
else
  _step "Build image in ACR"

  if [[ -z "$IMAGE_TAG" ]]; then
    GIT_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo 'nogit')"
    IMAGE_TAG="$(date +%Y%m%d-%H%M%S)-${GIT_SHA}"
  fi
  IMAGE_REF="${ACR_LOGIN}/payswitch-credit-scoring:${IMAGE_TAG}"
  _info "Building $IMAGE_REF"

  if ! az acr build \
       --registry "$ACR_NAME" \
       --image "payswitch-credit-scoring:${IMAGE_TAG}" \
       --file "$REPO_ROOT/Dockerfile" \
       "$REPO_ROOT" \
       --output none; then
    _fail "ACR build failed — check 'az acr task logs --registry $ACR_NAME'"
    exit 4
  fi
  _ok "Image pushed: $IMAGE_REF"

  _step "Update Container App"
  _info "Rolling out new revision for $ACA_APP"
  if ! az containerapp update \
       --name "$ACA_APP" \
       --resource-group "$RG_NAME" \
       --image "$IMAGE_REF" \
       --output none; then
    _fail "Container App update failed"
    exit 5
  fi
  _ok "Revision rolled out"

  # Wait for latest revision to become Provisioned + Healthy (max ~3 min).
  _info "Waiting for the new revision to become healthy..."
  for i in $(seq 1 36); do
    STATE="$(az containerapp revision list \
      --name "$ACA_APP" \
      --resource-group "$RG_NAME" \
      --query "[?properties.active && contains(properties.template.containers[0].image, '${IMAGE_TAG}')].properties.{prov:provisioningState, health:healthState}" \
      -o json 2>/dev/null || echo '[]')"
    PROV="$(echo "$STATE" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d[0].get("prov","") if d else "")' 2>/dev/null || echo "")"
    HEAL="$(echo "$STATE" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d[0].get("health","") if d else "")' 2>/dev/null || echo "")"
    if [[ "$PROV" == "Provisioned" && "$HEAL" == "Healthy" ]]; then
      _ok "Revision healthy after ${i}x5s poll"
      break
    fi
    if [[ $i -eq 36 ]]; then
      _warn "Revision not healthy after 3m — inspect 'az containerapp revision list'"
    fi
    sleep 5
  done
fi

# ==============================================================================
# 8. Smoke test
# ==============================================================================
_step "Smoke test"
SMOKE_URL="https://${ACA_FQDN}/health"
_info "GET $SMOKE_URL"

for i in 1 2 3 4 5; do
  if curl -fsS -m 10 "$SMOKE_URL" > /tmp/payswitch-smoke.json 2>/dev/null; then
    _ok "Response: $(cat /tmp/payswitch-smoke.json)"
    break
  fi
  if [[ $i -eq 5 ]]; then
    _fail "Health check failed after 5 retries"
    exit 6
  fi
  sleep 5
done

echo
_ok "${BLD}Deploy complete.${RST}"
echo "   API URL: https://${ACA_FQDN}"
echo "   Swagger: https://${ACA_FQDN}/docs"
echo "   Health:  https://${ACA_FQDN}/health"
echo
_info "Next steps:"
echo "   • Log in with the bootstrap admin credentials and rotate the password."
echo "   • Tighten Postgres firewall rules once the Container App outbound IP is stable."
echo "   • Subsequent code-only deploys: ./scripts/deploy.sh --skip-infra --yes"
