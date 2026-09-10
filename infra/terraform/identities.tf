resource "azurerm_user_assigned_identity" "runtime" {
  name                = local.runtime_identity_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.common_tags
}

resource "azurerm_user_assigned_identity" "ci" {
  name                = local.ci_identity_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.common_tags
}

resource "azurerm_federated_identity_credential" "github" {
  count = var.github_federation_enabled ? 1 : 0

  name                      = "github-deploy"
  user_assigned_identity_id = azurerm_user_assigned_identity.ci.id
  issuer                    = "https://token.actions.githubusercontent.com"
  subject                   = local.github_oidc_subject
  audience                  = ["api://AzureADTokenExchange"]
}

resource "azurerm_role_assignment" "ci_acr_push" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPush"
  principal_id         = azurerm_user_assigned_identity.ci.principal_id
  principal_type       = "ServicePrincipal"
  description          = "Allow the separate deployment identity to push versioned API images."
}

resource "azurerm_role_assignment" "ci_container_app_deploy" {
  scope                = azurerm_container_app.api.id
  role_definition_name = "Container Apps Contributor"
  principal_id         = azurerm_user_assigned_identity.ci.principal_id
  principal_type       = "ServicePrincipal"
  description          = "Allow the separate deployment identity to update only this Container App."
}

resource "azurerm_role_assignment" "runtime_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.runtime.principal_id
  principal_type       = "ServicePrincipal"
  description          = "Allow only the API runtime identity to pull images from this registry."
}

resource "azurerm_role_assignment" "runtime_key_vault_secrets" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.runtime.principal_id
  principal_type       = "ServicePrincipal"
  description          = "Allow the API runtime identity to read referenced runtime secrets."
}
