#!/bin/bash
# Azure Container Apps Deployment Script for PaySwitch Credit Scoring Backend
#
# This script provisions the necessary Azure resources:
# - Azure Resource Group
# - Log Analytics Workspace (for container logs)
# - Azure Container Registry (for hosting the Docker image)
# - Azure Container Apps Environment (managed serverless cluster)
# - Azure Container App (auto-scaling backend service)
#
# Ensure you are logged in to Azure CLI (`az login`) before running.

set -e

# ==========================================
# CONFIGURATION VARIABLES
# ==========================================
# Resource names must be lowercase alphanumeric for Container Apps
APP_NAME="payswitch-cs"
ENV_STAGE="prod"

# Azure Configuration
LOCATION="westus3"                 # e.g., westus3, eastus, westeurope
RG_NAME="rg-${APP_NAME}-${ENV_STAGE}"
ACR_NAME="acr${APP_NAME//-/}env"    # Cannot contain hyphens
LOG_ANALYTICS_NAME="log-${APP_NAME}-${ENV_STAGE}"
ACA_ENV_NAME="aca-env-${APP_NAME}-${ENV_STAGE}"
CONTAINER_APP_NAME="api-${APP_NAME}-${ENV_STAGE}"

# Auto-scaling Configuration
MIN_REPLICAS=1
MAX_REPLICAS=5
CONCURRENT_REQUESTS_SCALING=50   # Scale up when 50 concurrent HTTP requests hit an instance

# Application Environment Variables
# Ideally, you'd pull these from a Key Vault, but here's how to pass them
DB_URL="postgresql+asyncpg://<user>:<pwd>@<host>/<db>"
REDIS_URL="redis://<redis-host>:6379/0"
# (Fetch your existing service bus connection string)
# AZURE_SERVICEBUS_CONNECTION_STRING="..."

# ==========================================
# DEPLOYMENT STEPS
# ==========================================

echo "🚀 Starting Deployment for $CONTAINER_APP_NAME in $LOCATION..."

# 1. Create Resource Group
echo "📦 Creating Resource Group: $RG_NAME"
az group create --name "$RG_NAME" --location "$LOCATION" -o none

# 2. Create Log Analytics Workspace
echo "📊 Creating Log Analytics Workspace: $LOG_ANALYTICS_NAME"
az monitor log-analytics workspace create \
  --resource-group "$RG_NAME" \
  --workspace-name "$LOG_ANALYTICS_NAME" \
  --location "$LOCATION" -o none

LOG_ANALYTICS_ID=$(az monitor log-analytics workspace show --query customerId -g "$RG_NAME" -n "$LOG_ANALYTICS_NAME" -o tsv)
LOG_ANALYTICS_SECRET=$(az monitor log-analytics workspace get-shared-keys --query primarySharedKey -g "$RG_NAME" -n "$LOG_ANALYTICS_NAME" -o tsv)

# 3. Create Azure Container Registry (ACR)
echo "🐳 Creating Azure Container Registry: $ACR_NAME"
az acr create \
  --resource-group "$RG_NAME" \
  --name "$ACR_NAME" \
  --sku Basic \
  --admin-enabled true -o none

# 4. Build and Push Docker Image to ACR
#    (We use ACR Tasks to build the image remotely directly from your local source code)
echo "🔨 Building Docker image in Azure Container Registry..."
az acr build \
  --registry "$ACR_NAME" \
  --image "${CONTAINER_APP_NAME}:latest" \
  . 

# 5. Create Azure Container Apps Environment
echo "☁️ Creating Azure Container Apps Environment: $ACA_ENV_NAME"
az containerapp env create \
  --name "$ACA_ENV_NAME" \
  --resource-group "$RG_NAME" \
  --location "$LOCATION" \
  --logs-workspace-id "$LOG_ANALYTICS_ID" \
  --logs-workspace-key "$LOG_ANALYTICS_SECRET" -o none

# 6. Deploy backend to Azure Container App
echo "🚀 Deploying the application to Azure Container Apps..."
az containerapp create \
  --name "$CONTAINER_APP_NAME" \
  --resource-group "$RG_NAME" \
  --environment "$ACA_ENV_NAME" \
  --image "${ACR_NAME}.azurecr.io/${CONTAINER_APP_NAME}:latest" \
  --registry-server "${ACR_NAME}.azurecr.io" \
  --target-port 8000 \
  --ingress 'external' \
  --min-replicas $MIN_REPLICAS \
  --max-replicas $MAX_REPLICAS \
  --scale-rule-name "http-scaling-rule" \
  --scale-rule-type "http" \
  --scale-rule-http-concurrency $CONCURRENT_REQUESTS_SCALING \
  --env-vars \
    "DATABASE_URL=$DB_URL" \
    "REDIS_URL=$REDIS_URL" \
    "APP_ENV=production" \
    "DEBUG=false" \
    "ALLOWED_ORIGINS=[\"https://payswitch-cs.vercel.app\"]"

# ==========================================
# RETRIEVE APPLICATION URL
# ==========================================
FQDN=$(az containerapp show \
  --name "$CONTAINER_APP_NAME" \
  --resource-group "$RG_NAME" \
  --query properties.configuration.ingress.fqdn \
  --output tsv)

echo ""
echo "✅ Deployment Successful!"
echo "🔗 Your application is running at: https://$FQDN"
echo "📈 Scaling is configured: Minimum $MIN_REPLICAS, Maximum $MAX_REPLICAS (based on HTTP traffic)"
