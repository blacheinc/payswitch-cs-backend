#!/usr/bin/env bash
# ==============================================================================
# destroy.sh — Tear down the PaySwitch Azure deployment
#
# What it does:
#   1. Reads the resource group + Key Vault name from the parameters file
#      (or from command-line overrides).
#   2. Confirms with the operator (unless --yes).
#   3. Deletes the resource group.
#   4. Optionally purges the soft-deleted Key Vault so the same name can
#      be reused without waiting out the 7-day retention window.
#
# Usage:
#   scripts/destroy.sh [--yes] [--purge-vault] [--parameters FILE]
#
# Flags:
#   --yes           Skip the interactive confirmation.
#   --purge-vault   After RG delete, immediately purge the soft-deleted KV.
#   --parameters F  Override the default parameters file path.
# ==============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARAMS_FILE="$REPO_ROOT/infra/bicep/main.parameters.json"
ASSUME_YES=0
PURGE_VAULT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y)      ASSUME_YES=1; shift ;;
    --purge-vault) PURGE_VAULT=1; shift ;;
    --parameters)  PARAMS_FILE="$2"; shift 2 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown flag: $1" >&2; exit 1 ;;
  esac
done

RED=$(printf '\033[0;31m')
GRN=$(printf '\033[0;32m')
YEL=$(printf '\033[0;33m')
BLD=$(printf '\033[1m')
RST=$(printf '\033[0m')

if [[ ! -f "$PARAMS_FILE" ]]; then
  echo "${RED}[fail]${RST} Parameters file not found: $PARAMS_FILE" >&2
  exit 1
fi

RG_NAME="$(python3 -c 'import json,sys; p=json.load(open(sys.argv[1]))["parameters"]; print(p["resourceGroupName"]["value"])' "$PARAMS_FILE")"

# Find the Key Vault name (for optional purge) by scanning the RG *before*
# we delete it. The vault name is derived by the Bicep template and not in
# the parameters file.
KV_NAME=""
if az group exists --name "$RG_NAME" --output tsv 2>/dev/null | grep -q true; then
  KV_NAME="$(az keyvault list --resource-group "$RG_NAME" --query '[0].name' -o tsv 2>/dev/null || echo "")"
fi

echo "${BLD}Target:${RST}"
echo "  Resource group : $RG_NAME"
echo "  Key Vault      : ${KV_NAME:-<none or unreachable>}"
echo "  Purge vault    : $([[ $PURGE_VAULT -eq 1 ]] && echo yes || echo no)"
echo

if [[ $ASSUME_YES -eq 0 ]]; then
  echo "${YEL}${BLD}This will IRREVERSIBLY destroy all resources above.${RST}"
  read -r -p "Type the resource group name to confirm: " confirm
  if [[ "$confirm" != "$RG_NAME" ]]; then
    echo "${YEL}[abort]${RST} Confirmation mismatch — nothing deleted."
    exit 0
  fi
fi

echo "${BLD}══ Deleting resource group ══${RST}"
az group delete --name "$RG_NAME" --yes --no-wait
echo "${GRN}[ok]${RST} Delete command submitted (runs in background)."
echo "  Poll status: az group show --name $RG_NAME --query properties.provisioningState -o tsv"

if [[ $PURGE_VAULT -eq 1 && -n "$KV_NAME" ]]; then
  echo
  echo "${BLD}══ Waiting for soft-deleted vault to appear ══${RST}"
  for i in $(seq 1 30); do
    if az keyvault list-deleted --query "[?name=='${KV_NAME}'] | length(@)" -o tsv 2>/dev/null | grep -q '^1$'; then
      echo "${GRN}[ok]${RST} Vault soft-deleted after ${i}x10s polls; purging..."
      az keyvault purge --name "$KV_NAME"
      echo "${GRN}[ok]${RST} Vault purged — name '$KV_NAME' is reusable."
      exit 0
    fi
    sleep 10
  done
  echo "${YEL}[warn]${RST} Soft-delete did not appear within 5m — purge manually:"
  echo "        az keyvault purge --name $KV_NAME"
fi
