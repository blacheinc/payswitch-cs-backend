// Single Key-Vault secret helper.  Kept as its own module so ``main.bicep``
// can iterate over a materialized list of connection-string outputs and
// fan-out one resource per name/value pair.
//
// Secret names must match the kebab-case convention from
// app/config/keyvault_source.py (e.g. ``jwt-secret-key``,
// ``database-url``, ``azure-servicebus-connection-string``).
@description('Name of the Key Vault hosting the secret.')
param vaultName string

@description('Secret name (kebab-case, matches Settings field with underscores replaced).')
param secretName string

@description('Secret value.')
@secure()
param secretValue string

@description('Content type tag for the secret (optional).')
param contentType string = 'text/plain'

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: vaultName
}

resource secret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: secretName
  properties: {
    value: secretValue
    contentType: contentType
    attributes: {
      enabled: true
    }
  }
}

output id string = secret.id
output secretUri string = secret.properties.secretUri
