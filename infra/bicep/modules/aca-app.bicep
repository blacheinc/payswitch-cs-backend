// Container App hosting the FastAPI backend.
//
// The initial deploy uses a placeholder image from MCR so the resource
// comes up before the real image is built; the one-shot deploy script
// then runs ``az acr build`` against the repo and ``az containerapp
// update --image`` to swap in the real image.  This keeps Bicep idempotent
// and lets the client re-run ``az deployment`` without rebuilding.
@description('Azure region.')
param location string

@description('Container App name.')
param appName string

@description('Container Apps environment resource ID.')
param environmentId string

@description('ACR login server (e.g. myreg.azurecr.io).')
param acrLoginServer string

@description('ACR admin username.')
param acrUsername string

@description('ACR admin password.')
@secure()
param acrPassword string

@description('Image tag to deploy. Leave at the placeholder for first deploy; overwrite with the real image via ``az containerapp update`` after building.')
param imageRef string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Container CPU (cores).')
param cpu string = '0.5'

@description('Container memory (Gi).')
param memory string = '1.0Gi'

@description('Minimum replicas (≥ 1 because the batch worker runs in-process in this container).')
@minValue(1)
param minReplicas int = 1

@description('Maximum replicas for HTTP-based autoscaling.')
@minValue(1)
param maxReplicas int = 5

@description('Concurrent requests per replica before scaling out.')
param httpScaleConcurrency int = 50

@description('External port exposed by the container.')
param targetPort int = 8000

// ── Runtime config ────────────────────────────────────────────────────────
// Non-secret values passed as plain env vars.  Secrets live in Key Vault
// and the app resolves them via the ``KeyVaultSettingsSource``.

@description('APP_ENV — "production" in client deploys.')
param appEnv string = 'production'

@description('CORS origins comma-separated, e.g. "https://dashboard.client.com".')
param allowedOrigins string = ''

@description('Key Vault URL consumed by the app at runtime.')
param azureKeyVaultUrl string

@description('AAD tenant ID for the Key Vault service principal.')
param azureTenantId string

@description('Client ID of the Key Vault service principal.')
param azureClientId string

@description('Client secret of the Key Vault service principal.')
@secure()
param azureClientSecret string

@description('Dashboard base URL (email links).')
param dashboardBaseUrl string

@description('Email-from address (must be verified with Resend).')
param emailFrom string

@description('DE inference function URL.')
param deInferenceUrl string

@description('Orchestrator function URL.')
param orchestratorBaseUrl string

@description('Customer service function URL.')
param customerServiceBaseUrl string = ''

@description('Bootstrap admin email (first-boot seeder only).')
param bootstrapAdminEmail string = ''

@description('Bootstrap admin password.')
@secure()
param bootstrapAdminPassword string = ''

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  properties: {
    environmentId: environmentId
    configuration: {
      ingress: {
        external: true
        targetPort: targetPort
        transport: 'auto'
        allowInsecure: false
      }
      // Registry creds + SP secret + bootstrap password are stored as
      // Container App secrets (runtime-encrypted).  Env vars reference
      // them via ``secretRef``.
      registries: [
        {
          server: acrLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acrPassword
        }
        {
          name: 'azure-client-secret'
          value: azureClientSecret
        }
        {
          name: 'bootstrap-admin-password'
          value: bootstrapAdminPassword
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: imageRef
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: [
            { name: 'APP_ENV',                   value: appEnv }
            { name: 'PORT',                      value: string(targetPort) }
            { name: 'ALLOWED_ORIGINS',           value: allowedOrigins }
            { name: 'AZURE_KEYVAULT_URL',        value: azureKeyVaultUrl }
            { name: 'AZURE_TENANT_ID',           value: azureTenantId }
            { name: 'AZURE_CLIENT_ID',           value: azureClientId }
            { name: 'AZURE_CLIENT_SECRET',       secretRef: 'azure-client-secret' }
            { name: 'DASHBOARD_BASE_URL',        value: dashboardBaseUrl }
            { name: 'EMAIL_FROM',                value: emailFrom }
            { name: 'DE_INFERENCE_URL',          value: deInferenceUrl }
            { name: 'ORCHESTRATOR_BASE_URL',     value: orchestratorBaseUrl }
            { name: 'CUSTOMER_SERVICE_BASE_URL', value: customerServiceBaseUrl }
            { name: 'BOOTSTRAP_ADMIN_EMAIL',     value: bootstrapAdminEmail }
            { name: 'BOOTSTRAP_ADMIN_PASSWORD',  secretRef: 'bootstrap-admin-password' }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: targetPort
              }
              initialDelaySeconds: 30
              periodSeconds: 30
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: targetPort
              }
              initialDelaySeconds: 10
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: string(httpScaleConcurrency)
              }
            }
          }
        ]
      }
    }
  }
}

output appName string = app.name
output fqdn string = app.properties.configuration.ingress.fqdn
output latestRevisionName string = app.properties.latestRevisionName
