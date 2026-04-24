// Azure Key Vault — holds every secret the app reads via the
// ``KeyVaultSettingsSource`` (see app/config/keyvault_source.py).  Access
// is granted to a caller-supplied service principal using RBAC
// (``Key Vault Secrets User``).  The app reads secrets with that SP's
// client-id/secret, which is the closest Key-Vault-native equivalent to
// a connection string.
@description('Azure region.')
param location string

@description('Key Vault name — globally unique, 3-24 alphanumeric/hyphen chars.')
@minLength(3)
@maxLength(24)
param name string

@description('Tenant ID — usually subscription().tenantId from the caller.')
param tenantId string

@description('Object ID (NOT app ID) of the service principal that will read secrets at runtime. Granted Key Vault Secrets User.')
param appPrincipalObjectId string

@description('Additional object IDs granted Key Vault Secrets Officer (typically the human operator running the deploy).')
param adminObjectIds array = []

@description('Use RBAC authorization instead of legacy access policies.')
param enableRbacAuthorization bool = true

@description('Enable soft-delete (recommended on — required by Azure since 2020).')
param enableSoftDelete bool = true

@description('Soft-delete retention in days.')
@minValue(7)
@maxValue(90)
param softDeleteRetentionDays int = 7

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: name
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: tenantId
    enableRbacAuthorization: enableRbacAuthorization
    enableSoftDelete: enableSoftDelete
    softDeleteRetentionInDays: softDeleteRetentionDays
    enablePurgeProtection: false
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

// ── Role assignments ────────────────────────────────────────────────────────
// Built-in role IDs (subscription-agnostic).  Docs:
// https://learn.microsoft.com/azure/role-based-access-control/built-in-roles
var secretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
var secretsOfficerRoleId = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

resource appRuntimeAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, appPrincipalObjectId, secretsUserRoleId)
  scope: vault
  properties: {
    principalId: appPrincipalObjectId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      secretsUserRoleId
    )
  }
}

resource adminAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for oid in adminObjectIds: {
    name: guid(vault.id, oid, secretsOfficerRoleId)
    scope: vault
    properties: {
      principalId: oid
      roleDefinitionId: subscriptionResourceId(
        'Microsoft.Authorization/roleDefinitions',
        secretsOfficerRoleId
      )
    }
  }
]

output id string = vault.id
output name string = vault.name
output vaultUri string = vault.properties.vaultUri
