// Container Apps environment — the logical boundary for the API app.
// Logs ship to a Log Analytics workspace; scaling is on by default.
@description('Azure region.')
param location string

@description('Environment name.')
param name string

@description('Log Analytics customer (workspace) ID.')
param logAnalyticsWorkspaceId string

@description('Log Analytics primary shared key.')
@secure()
param logAnalyticsSharedKey string

resource acaEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: name
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsWorkspaceId
        sharedKey: logAnalyticsSharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

output id string = acaEnv.id
output name string = acaEnv.name
output defaultDomain string = acaEnv.properties.defaultDomain
