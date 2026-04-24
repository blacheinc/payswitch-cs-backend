// Azure Service Bus — namespace with the five topics the app publishes /
// subscribes to for the ML pipeline and scoring events, plus two
// subscriptions consumed by the backend.
//
// Topic / subscription names match the defaults in app/config/__init__.py.
// Override via the ``topicNames`` / ``subscriptionNames`` parameters if
// the client wants non-default names.
@description('Azure region.')
param location string

@description('Namespace name — globally unique, 6-50 alphanumeric/hyphen chars.')
@minLength(6)
@maxLength(50)
param namespaceName string

@description('Service Bus SKU.')
@allowed([
  'Basic'
  'Standard'
  'Premium'
])
param sku string = 'Standard'

@description('Topic names matching Settings defaults.')
param topicNames object = {
  trainingStarted: 'model-training-started'
  trainingCompleted: 'model-training-completed'
  scoringComplete: 'scoring-complete'
  scoreApplicant: 'score-applicant'
  batchScoreRequest: 'batch-score-request'
  batchScoreComplete: 'batch-score-complete'
}

@description('Subscription names consumed by the backend.')
param subscriptionNames object = {
  backend: 'backend-sub'
  csBackend: 'cs-backend'
}

resource namespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: namespaceName
  location: location
  sku: {
    name: sku
    tier: sku
  }
  properties: {
    disableLocalAuth: false // we use SAS / connection strings, not AAD
    minimumTlsVersion: '1.2'
  }
}

var topicList = [
  topicNames.trainingStarted
  topicNames.trainingCompleted
  topicNames.scoringComplete
  topicNames.scoreApplicant
  topicNames.batchScoreRequest
  topicNames.batchScoreComplete
]

resource topics 'Microsoft.ServiceBus/namespaces/topics@2022-10-01-preview' = [
  for t in topicList: {
    parent: namespace
    name: t
    properties: {
      defaultMessageTimeToLive: 'P14D'
      enableBatchedOperations: true
      supportOrdering: false
    }
  }
]

// Subscriptions on the topics the backend actually consumes from.
// Per app/main.py lifespan:
//   - ``training-started``    → cs-backend
//   - ``training-completed``  → cs-backend
//   - ``scoring-complete``    → backend
resource subTrainingStarted 'Microsoft.ServiceBus/namespaces/topics/subscriptions@2022-10-01-preview' = {
  parent: topics[0]
  name: subscriptionNames.csBackend
  properties: {
    maxDeliveryCount: 10
    lockDuration: 'PT1M'
    defaultMessageTimeToLive: 'P14D'
    deadLetteringOnMessageExpiration: true
  }
}

resource subTrainingCompleted 'Microsoft.ServiceBus/namespaces/topics/subscriptions@2022-10-01-preview' = {
  parent: topics[1]
  name: subscriptionNames.csBackend
  properties: {
    maxDeliveryCount: 10
    lockDuration: 'PT1M'
    defaultMessageTimeToLive: 'P14D'
    deadLetteringOnMessageExpiration: true
  }
}

resource subScoringComplete 'Microsoft.ServiceBus/namespaces/topics/subscriptions@2022-10-01-preview' = {
  parent: topics[2]
  name: subscriptionNames.backend
  properties: {
    maxDeliveryCount: 10
    lockDuration: 'PT1M'
    defaultMessageTimeToLive: 'P14D'
    deadLetteringOnMessageExpiration: true
  }
}

// Use the built-in RootManageSharedAccessKey for a quick start; rotate to
// a less-privileged SAS key post-deploy if the client requires it.
resource rootAuthRule 'Microsoft.ServiceBus/namespaces/authorizationRules@2022-10-01-preview' existing = {
  parent: namespace
  name: 'RootManageSharedAccessKey'
}

output namespaceName string = namespace.name

@description('Connection string with Manage rights used by the app.')
@secure()
output connectionString string = rootAuthRule.listKeys().primaryConnectionString
