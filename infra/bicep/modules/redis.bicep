// Azure Cache for Redis — used by the app for rate limiting, idempotency
// caching, and the batch worker queue.  Basic C0 (250 MB, single node)
// is the smallest/cheapest tier; upgrade to Standard for HA.
@description('Azure region.')
param location string

@description('Cache name — globally unique, 1-63 alphanumeric/hyphen chars.')
@minLength(1)
@maxLength(63)
param name string

@description('Redis SKU family.')
@allowed([
  'C' // Basic / Standard
  'P' // Premium
])
param skuFamily string = 'C'

@description('Redis SKU name.')
@allowed([
  'Basic'
  'Standard'
  'Premium'
])
param skuName string = 'Basic'

@description('Redis SKU capacity (0-6 for C family; maps to 250MB-53GB).')
@minValue(0)
@maxValue(6)
param skuCapacity int = 0

resource cache 'Microsoft.Cache/redis@2023-08-01' = {
  name: name
  location: location
  properties: {
    sku: {
      name: skuName
      family: skuFamily
      capacity: skuCapacity
    }
    enableNonSslPort: false
    minimumTlsVersion: '1.2'
    redisConfiguration: {
      'maxmemory-policy': 'allkeys-lru'
    }
  }
}

output id string = cache.id
output hostName string = cache.properties.hostName
output sslPort int = cache.properties.sslPort

@description('Full URL consumed by the app (rediss://:key@host:port/0).')
@secure()
output connectionUrl string = 'rediss://:${cache.listKeys().primaryKey}@${cache.properties.hostName}:${cache.properties.sslPort}/0'
