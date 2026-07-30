param location string
param tags object
param containerImage string
param deployerObjectId string
param containerAppName string
param appInsightsName string
param keyVaultName string
param acrResourceName string
param caeResourceName string
param laResourceName string
param languageEndpoint string
param languageResourceName string

var isPlaceholder = containerImage == 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
var targetPort = isPlaceholder ? 80 : 8000

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' existing = {
  name: laResourceName
}

resource managedEnvironment 'Microsoft.App/managedEnvironments@2026-01-01' existing = {
  name: caeResourceName
}

resource registry 'Microsoft.ContainerRegistry/registries@2025-11-01' existing = {
  name: acrResourceName
}

resource language 'Microsoft.CognitiveServices/accounts@2026-05-01' existing = {
  name: languageResourceName
}

resource keyVault 'Microsoft.KeyVault/vaults@2026-02-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource containerApp 'Microsoft.App/containerApps@2026-01-01' = {
  name: containerAppName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    environmentId: managedEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: targetPort
        allowInsecure: false
      }
      registries: isPlaceholder
        ? []
        : [
            {
              server: registry.properties.loginServer
              identity: 'system'
            }
          ]
    }
    template: {
      containers: [
        {
          name: 'app'
          image: containerImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: isPlaceholder
            ? []
            : [
                {
                  name: 'LANGUAGE_ENDPOINT'
                  value: languageEndpoint
                }
                {
                  name: 'AZURE_REGION'
                  value: location
                }
                {
                  name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
                  value: appInsights.properties.ConnectionString
                }
              ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: isPlaceholder ? '/' : '/healthz'
                port: targetPort
                scheme: 'HTTP'
              }
              initialDelaySeconds: 10
              periodSeconds: 30
              timeoutSeconds: 5
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: isPlaceholder ? '/' : '/healthz'
                port: targetPort
                scheme: 'HTTP'
              }
              initialDelaySeconds: 5
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 3
      }
    }
  }
}

resource kvDeployerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, deployerObjectId, 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
    )
    principalId: deployerObjectId
    principalType: 'User'
  }
}

resource acrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, containerApp.id, '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d'
    )
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource languageUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(language.id, containerApp.id, 'a97b65f3-24c7-4388-baec-2e87135dc908')
  scope: language
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'a97b65f3-24c7-4388-baec-2e87135dc908'
    )
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output keyVaultName string = keyVault.name
output containerAppPrincipalId string = containerApp.identity.principalId
