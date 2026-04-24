// Azure Container Registry — holds the API container image.
// Basic SKU keeps cost low; upgrade to Standard/Premium if the client
// needs geo-replication or private endpoints later.
@description('Azure region.')
param location string

@description('ACR name — globally unique, alphanumeric, 5-50 chars.')
@minLength(5)
@maxLength(50)
param name string

@description('SKU tier for the registry.')
@allowed([
  'Basic'
  'Standard'
  'Premium'
])
param sku string = 'Basic'

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: name
  location: location
  sku: {
    name: sku
  }
  properties: {
    // Admin user is required for connection-string-style auth from ACA
    // (so ACA can pull the image with a username/password rather than a
    // managed identity).  Fits the "connection strings everywhere"
    // directive for this deployment.
    adminUserEnabled: true
  }
}

output id string = registry.id
output loginServer string = registry.properties.loginServer
#disable-next-line outputs-should-not-contain-secrets
output adminUsername string = registry.listCredentials().username

@secure()
output adminPassword string = registry.listCredentials().passwords[0].value
