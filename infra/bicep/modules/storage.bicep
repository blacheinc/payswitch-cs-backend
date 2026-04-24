// Azure Storage account — holds bulk uploads, training data, reports, and
// champion model artifacts.  Single StorageV2 account with four
// containers keeps the deployment surface minimal; the app's Settings
// allow pointing the ``champion_storage_connection_string`` at a second
// account if the client wants physical isolation of ML artifacts later.
@description('Azure region.')
param location string

@description('Storage account name — 3-24 lowercase alphanumeric.')
@minLength(3)
@maxLength(24)
param name string

@description('Storage SKU.')
@allowed([
  'Standard_LRS'
  'Standard_ZRS'
  'Standard_GRS'
])
param sku string = 'Standard_LRS'

var containerNames = [
  'bulk-uploads'
  'training-data'
  'reports'
  'model-artifacts'
]

resource account 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: name
  location: location
  sku: {
    name: sku
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: account
  name: 'default'
}

resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [
  for c in containerNames: {
    parent: blobService
    name: c
    properties: {
      publicAccess: 'None'
    }
  }
]

output id string = account.id
output name string = account.name

@description('Connection string for the app to use.')
@secure()
output connectionString string = 'DefaultEndpointsProtocol=https;AccountName=${account.name};AccountKey=${account.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
