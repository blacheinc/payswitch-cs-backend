// PaySwitch Credit Scoring — one-shot Azure deployment.
//
// Subscription-scope Bicep that provisions:
//
//   • Resource group
//   • Log Analytics workspace
//   • Container Registry (ACR)
//   • Storage account (4 containers)
//   • PostgreSQL Flexible Server + ``credit_scoring`` database
//   • Redis Cache
//   • Service Bus namespace + 6 topics + 3 subscriptions
//   • Key Vault (+ role assignments for the runtime SP and the operator)
//   • All runtime secrets seeded into the Key Vault
//   • Container Apps environment + Container App (placeholder image)
//
// The caller runs ``az deployment sub create`` against this file.  See
// infra/bicep/README.md for the full recipe.  After the Bicep completes,
// the companion script (``scripts/deploy.sh``) builds the real image in
// ACR and swaps it onto the Container App — this file does NOT build
// anything, only provisions.
//
// NOTE: all Azure data-plane auth is via connection strings / keys (no
// managed identity) per the project's operational directive.  The one
// exception is Key Vault itself, which only supports AAD; the caller
// supplies a service principal that the app uses as its KV credential.
targetScope = 'subscription'

// ── Core identity ────────────────────────────────────────────────────────────
@description('Azure region for every resource.')
param location string

@description('Resource group name (created if missing).')
param resourceGroupName string

@description('Short prefix used as the stem for every resource name (3-12 lowercase alphanumeric).  Example: "psc".')
@minLength(3)
@maxLength(12)
param namePrefix string

@description('Environment identifier appended to names, e.g. "prod" or "stg".')
@allowed([
  'dev'
  'stg'
  'prod'
])
param environment string = 'prod'

// Derive consistent per-resource names.  Some resource types have
// globally-unique constraints so we tack on a short hash of the
// subscription + RG to avoid collisions between separate client deploys.
var nameSuffix = uniqueString(subscription().id, resourceGroupName)
var shortNameSuffix = substring(nameSuffix, 0, 6)
var baseName = toLower('${namePrefix}-${environment}')

var names = {
  logAnalytics: '${baseName}-log'
  acr:          replace('${namePrefix}${environment}acr${shortNameSuffix}', '-', '')
  storage:      replace('${namePrefix}${environment}st${shortNameSuffix}', '-', '')
  postgres:     '${baseName}-pg-${shortNameSuffix}'
  redis:        '${baseName}-redis-${shortNameSuffix}'
  serviceBus:   '${baseName}-sb-${shortNameSuffix}'
  keyVault:     '${namePrefix}${environment}kv${shortNameSuffix}'
  acaEnv:       '${baseName}-env'
  acaApp:       '${baseName}-api'
}

// ── Service principal for Key Vault access ───────────────────────────────────
@description('Object ID (NOT app ID) of the service principal the app uses to read Key Vault secrets. Create this SP before running the deploy (see README).')
param appPrincipalObjectId string

@description('Application (client) ID of the same service principal — injected into the Container App as AZURE_CLIENT_ID.')
param appClientId string

@description('Client secret value for the service principal — injected as AZURE_CLIENT_SECRET.')
@secure()
param appClientSecret string

@description('Optional AAD object IDs (humans / groups) granted Key Vault Secrets Officer so the operator can view/rotate secrets.')
param adminObjectIds array = []

// ── Data-plane secrets the operator provides ─────────────────────────────────
@description('Postgres admin login name.')
param postgresAdminLogin string = 'psadmin'

@description('Postgres admin password — 8+ chars, mix of upper/lower/digit/symbol.')
@secure()
param postgresAdminPassword string

@description('JWT signing key — generate a strong random string (≥ 32 chars).')
@secure()
param jwtSecretKey string

@description('XDS bureau username.')
@secure()
param xdsUsername string

@description('XDS bureau password.')
@secure()
param xdsPassword string

@description('Resend API key for transactional email (leave blank to fall back to SMTP/console).')
@secure()
param resendApiKey string = ''

// ── Downstream service URLs (not secrets) ────────────────────────────────────
@description('DE inference Function App URL (from the DE/ML team).')
param deInferenceUrl string

@description('DE inference function code / host key.')
@secure()
param deInferenceCode string

@description('Orchestrator Function App base URL.')
param orchestratorBaseUrl string

@description('Orchestrator function host key.')
@secure()
param orchestratorFunctionKey string

@description('Customer Service Function App base URL (optional).')
param customerServiceBaseUrl string = ''

@description('Customer Service function host key (optional).')
@secure()
param customerServiceFunctionKey string = ''

// ── App configuration ───────────────────────────────────────────────────────
@description('Dashboard base URL used in transactional email links.')
param dashboardBaseUrl string

@description('From-address for outbound email (must be verified with the email provider).')
param emailFrom string

@description('Comma-separated CORS origins for the API.')
param allowedOrigins string = ''

@description('Bootstrap admin email minted on first boot (leave blank to skip).')
param bootstrapAdminEmail string = ''

@description('Bootstrap admin password (must be rotated immediately after first login).')
@secure()
param bootstrapAdminPassword string = ''

// ── Resource group ──────────────────────────────────────────────────────────
resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: resourceGroupName
  location: location
}

// ── Log Analytics ───────────────────────────────────────────────────────────
module logAnalytics 'modules/log-analytics.bicep' = {
  name: 'logAnalytics'
  scope: rg
  params: {
    location: location
    name: names.logAnalytics
  }
}

// ── ACR ─────────────────────────────────────────────────────────────────────
module acr 'modules/acr.bicep' = {
  name: 'acr'
  scope: rg
  params: {
    location: location
    name: names.acr
  }
}

// ── Storage ─────────────────────────────────────────────────────────────────
module storage 'modules/storage.bicep' = {
  name: 'storage'
  scope: rg
  params: {
    location: location
    name: names.storage
  }
}

// ── Postgres ────────────────────────────────────────────────────────────────
module postgres 'modules/postgres.bicep' = {
  name: 'postgres'
  scope: rg
  params: {
    location: location
    serverName: names.postgres
    administratorLogin: postgresAdminLogin
    administratorPassword: postgresAdminPassword
  }
}

// ── Redis ───────────────────────────────────────────────────────────────────
module redis 'modules/redis.bicep' = {
  name: 'redis'
  scope: rg
  params: {
    location: location
    name: names.redis
  }
}

// ── Service Bus ─────────────────────────────────────────────────────────────
module serviceBus 'modules/servicebus.bicep' = {
  name: 'serviceBus'
  scope: rg
  params: {
    location: location
    namespaceName: names.serviceBus
  }
}

// ── Key Vault ───────────────────────────────────────────────────────────────
module keyVault 'modules/keyvault.bicep' = {
  name: 'keyVault'
  scope: rg
  params: {
    location: location
    name: names.keyVault
    tenantId: subscription().tenantId
    appPrincipalObjectId: appPrincipalObjectId
    adminObjectIds: adminObjectIds
  }
}

// ── Seed secrets into Key Vault ─────────────────────────────────────────────
// Secret names follow the kebab-case convention the app uses in
// app/config/keyvault_source.py: Python field ``jwt_secret_key`` →
// vault secret ``jwt-secret-key``.
//
// Each secret is declared as its own module invocation rather than a
// for-loop because Bicep rejects iterating over values that depend on
// another module's outputs (they aren't known at deploy-start) — see
// BCP178.

module secretDatabaseUrl 'modules/secret.bicep' = {
  name: 'secret-database-url'
  scope: rg
  params: {
    vaultName: keyVault.outputs.name
    secretName: 'database-url'
    secretValue: postgres.outputs.connectionString
  }
}

module secretRedisUrl 'modules/secret.bicep' = {
  name: 'secret-redis-url'
  scope: rg
  params: {
    vaultName: keyVault.outputs.name
    secretName: 'redis-url'
    secretValue: redis.outputs.connectionUrl
  }
}

module secretServiceBus 'modules/secret.bicep' = {
  name: 'secret-servicebus'
  scope: rg
  params: {
    vaultName: keyVault.outputs.name
    secretName: 'azure-servicebus-connection-string'
    secretValue: serviceBus.outputs.connectionString
  }
}

module secretStorage 'modules/secret.bicep' = {
  name: 'secret-storage'
  scope: rg
  params: {
    vaultName: keyVault.outputs.name
    secretName: 'azure-storage-connection-string'
    secretValue: storage.outputs.connectionString
  }
}

module secretChampionStorage 'modules/secret.bicep' = {
  name: 'secret-champion-storage'
  scope: rg
  params: {
    vaultName: keyVault.outputs.name
    secretName: 'champion-storage-connection-string'
    secretValue: storage.outputs.connectionString
  }
}

module secretJwt 'modules/secret.bicep' = {
  name: 'secret-jwt'
  scope: rg
  params: {
    vaultName: keyVault.outputs.name
    secretName: 'jwt-secret-key'
    secretValue: jwtSecretKey
  }
}

module secretXdsUsername 'modules/secret.bicep' = {
  name: 'secret-xds-username'
  scope: rg
  params: {
    vaultName: keyVault.outputs.name
    secretName: 'xds-username'
    secretValue: xdsUsername
  }
}

module secretXdsPassword 'modules/secret.bicep' = {
  name: 'secret-xds-password'
  scope: rg
  params: {
    vaultName: keyVault.outputs.name
    secretName: 'xds-password'
    secretValue: xdsPassword
  }
}

module secretDeInference 'modules/secret.bicep' = {
  name: 'secret-de-inference'
  scope: rg
  params: {
    vaultName: keyVault.outputs.name
    secretName: 'de-inference-code'
    secretValue: deInferenceCode
  }
}

module secretOrchestrator 'modules/secret.bicep' = {
  name: 'secret-orchestrator'
  scope: rg
  params: {
    vaultName: keyVault.outputs.name
    secretName: 'orchestrator-function-key'
    secretValue: orchestratorFunctionKey
  }
}

module secretCustomerService 'modules/secret.bicep' = {
  name: 'secret-customer-service'
  scope: rg
  params: {
    vaultName: keyVault.outputs.name
    secretName: 'customer-service-function-key'
    secretValue: customerServiceFunctionKey
  }
}

module secretResend 'modules/secret.bicep' = {
  name: 'secret-resend'
  scope: rg
  params: {
    vaultName: keyVault.outputs.name
    secretName: 'resend-api-key'
    secretValue: resendApiKey
  }
}

// ── Container Apps environment + app ────────────────────────────────────────
module acaEnv 'modules/aca-env.bicep' = {
  name: 'acaEnv'
  scope: rg
  params: {
    location: location
    name: names.acaEnv
    logAnalyticsWorkspaceId: logAnalytics.outputs.customerId
    logAnalyticsSharedKey: logAnalytics.outputs.primarySharedKey
  }
}

module acaApp 'modules/aca-app.bicep' = {
  name: 'acaApp'
  scope: rg
  dependsOn: [
    secretDatabaseUrl
    secretRedisUrl
    secretServiceBus
    secretStorage
    secretChampionStorage
    secretJwt
    secretXdsUsername
    secretXdsPassword
    secretDeInference
    secretOrchestrator
    secretCustomerService
    secretResend
  ]
  params: {
    location: location
    appName: names.acaApp
    environmentId: acaEnv.outputs.id
    acrLoginServer: acr.outputs.loginServer
    acrUsername: acr.outputs.adminUsername
    acrPassword: acr.outputs.adminPassword
    appEnv: environment == 'prod' ? 'production' : (environment == 'stg' ? 'staging' : 'development')
    allowedOrigins: allowedOrigins
    azureKeyVaultUrl: keyVault.outputs.vaultUri
    azureTenantId: subscription().tenantId
    azureClientId: appClientId
    azureClientSecret: appClientSecret
    dashboardBaseUrl: dashboardBaseUrl
    emailFrom: emailFrom
    deInferenceUrl: deInferenceUrl
    orchestratorBaseUrl: orchestratorBaseUrl
    customerServiceBaseUrl: customerServiceBaseUrl
    bootstrapAdminEmail: bootstrapAdminEmail
    bootstrapAdminPassword: bootstrapAdminPassword
  }
}

// ── Outputs (consumed by the deploy script) ─────────────────────────────────
output resourceGroupName string = rg.name
output acrLoginServer string = acr.outputs.loginServer
output acrName string = names.acr
output acaAppName string = acaApp.outputs.appName
output acaAppFqdn string = acaApp.outputs.fqdn
output keyVaultUri string = keyVault.outputs.vaultUri
output keyVaultName string = keyVault.outputs.name
output postgresFqdn string = postgres.outputs.serverFqdn
output storageAccountName string = storage.outputs.name
