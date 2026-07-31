targetScope = 'subscription'

@minLength(1)
param environmentName string

@minLength(1)
param location string

param sessionId string
param deployedBy string
param createdAt string

@secure()
param deployerObjectId string

param resourceGroupName string
param containerAppName string
param appInsightsName string
param keyVaultName string
param languageEndpoint string
param languageResourceName string
param containerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

var tags = {
  'app-onboard-skill': 'true'
  'app-onboard-session-id': sessionId
  'created-at': createdAt
  environment: environmentName
  'deployed-by': deployedBy
}

resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' existing = {
  name: resourceGroupName
}

module resources 'modules/resources.bicep' = {
  name: 'language-pii-resources'
  scope: rg
  params: {
    location: location
    tags: tags
    containerImage: containerImage
    deployerObjectId: deployerObjectId
    containerAppName: containerAppName
    appInsightsName: appInsightsName
    keyVaultName: keyVaultName
    acrResourceName: 'regngasdf'
    caeResourceName: 'phia-ca-env'
    laResourceName: 'workspace-financeappng0vTV'
    languageEndpoint: languageEndpoint
    languageResourceName: languageResourceName
  }
}

output containerAppFqdn string = resources.outputs.containerAppFqdn
output keyVaultName string = resources.outputs.keyVaultName
output containerAppPrincipalId string = resources.outputs.containerAppPrincipalId
