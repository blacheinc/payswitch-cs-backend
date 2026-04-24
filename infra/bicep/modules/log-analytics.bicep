// Log Analytics workspace — used as the log backend for the Container Apps
// environment.  30-day retention is the cheapest tier that still gives
// operators enough history to diagnose a bad deploy.
@description('Azure region for the workspace (inherits from parent by default).')
param location string

@description('Globally-unique workspace name.')
param name string

@description('Workspace retention in days (30-730).')
@minValue(30)
@maxValue(730)
param retentionInDays int = 30

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: name
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

output id string = workspace.id
output customerId string = workspace.properties.customerId

@description('Primary shared key for the workspace — fed into the ACA env.')
#disable-next-line outputs-should-not-contain-secrets
output primarySharedKey string = workspace.listKeys().primarySharedKey
