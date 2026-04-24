// Azure Database for PostgreSQL — Flexible Server.
// Single database named "credit_scoring" matching the default
// ``database_url`` in app Settings.  Admin auth via
// username+password (connection-string-style, per the project's
// "always connection strings for Azure" directive).
@description('Azure region.')
param location string

@description('Server name — globally unique, 3-63 lowercase chars.')
@minLength(3)
@maxLength(63)
param serverName string

@description('Postgres admin username.')
param administratorLogin string

@description('Postgres admin password.')
@secure()
param administratorPassword string

@description('Postgres major version.')
@allowed([
  '14'
  '15'
  '16'
])
param version string = '16'

@description('Compute SKU name, e.g. Standard_B1ms (burstable) or Standard_D2s_v3.')
param skuName string = 'Standard_B2ms'

@description('Compute SKU tier.')
@allowed([
  'Burstable'
  'GeneralPurpose'
  'MemoryOptimized'
])
param skuTier string = 'Burstable'

@description('Storage size in GiB (minimum 32).')
@minValue(32)
param storageSizeGB int = 32

@description('Database name created on first boot.')
param databaseName string = 'credit_scoring'

@description('Start of the allow-list CIDR range for inbound connections. Use "0.0.0.0" with endIp 0.0.0.0 to only allow Azure-internal traffic.')
param firewallStartIp string = '0.0.0.0'

@description('End of the allow-list CIDR range for inbound connections. Default 0.0.0.0 + 0.0.0.0 = only Azure services.')
param firewallEndIp string = '0.0.0.0'

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview' = {
  name: serverName
  location: location
  sku: {
    name: skuName
    tier: skuTier
  }
  properties: {
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorPassword
    version: version
    storage: {
      storageSizeGB: storageSizeGB
      autoGrow: 'Enabled'
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    authConfig: {
      activeDirectoryAuth: 'Disabled'
      passwordAuth: 'Enabled'
    }
  }
}

// Allow Azure-internal traffic so the Container App can reach the DB.
// The "AllowAllAzureServices" rule is the Microsoft-documented shortcut
// (start=0.0.0.0, end=0.0.0.0) — it means "any Azure service in any
// subscription".  Tighten via VNet integration in a v2.
resource allowAzureAccess 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-06-01-preview' = {
  parent: server
  name: 'AllowAllAzureServices'
  properties: {
    startIpAddress: firewallStartIp
    endIpAddress: firewallEndIp
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-06-01-preview' = {
  parent: server
  name: databaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

output serverFqdn string = server.properties.fullyQualifiedDomainName
output serverName string = server.name
output databaseName string = database.name

@description('asyncpg connection string for the app.')
@secure()
output connectionString string = 'postgresql+asyncpg://${administratorLogin}:${administratorPassword}@${server.properties.fullyQualifiedDomainName}:5432/${database.name}?ssl=require'
