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

resource "azurerm_user_assigned_identity" "migration" {
  count = local.migration_enabled ? 1 : 0

  name                = local.migration_identity_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.common_tags
}

resource "azurerm_user_assigned_identity" "policy_index" {
  count = local.policy_index_enabled ? 1 : 0

  name                = local.policy_index_identity_name
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
  count = local.application_enabled ? 1 : 0

  scope                = azurerm_container_app.api[0].id
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
  count = local.application_enabled ? 2 : 0

  scope = count.index == 0 ? (
    "${azurerm_key_vault.main.id}/secrets/${coalesce(local.postgresql_application_secret_name, "not-configured")}"
    ) : (
    "${azurerm_key_vault.main.id}/secrets/${coalesce(local.huggingface_token_secret_name, "not-configured")}"
  )
  role_definition_id = local.key_vault_secrets_user_role_definition_id
  principal_id       = azurerm_user_assigned_identity.runtime.principal_id
  principal_type     = "ServicePrincipal"
  description        = "Allow the API runtime identity to read only one referenced runtime secret."
}

resource "azurerm_role_assignment" "migration_acr_pull" {
  count = local.migration_enabled ? 1 : 0

  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.migration[0].principal_id
  principal_type       = "ServicePrincipal"
  description          = "Allow only the database migration identity to pull its immutable image."
}

resource "azurerm_role_assignment" "migration_key_vault_secrets" {
  count = local.migration_enabled ? 2 : 0

  scope = count.index == 0 ? (
    "${azurerm_key_vault.main.id}/secrets/${coalesce(local.postgresql_administrator_secret_name, "not-configured")}"
    ) : (
    "${azurerm_key_vault.main.id}/secrets/${coalesce(local.postgresql_application_secret_name, "not-configured")}"
  )
  role_definition_id = local.key_vault_secrets_user_role_definition_id
  principal_id       = azurerm_user_assigned_identity.migration[0].principal_id
  principal_type     = "ServicePrincipal"
  description        = "Allow the migration identity to read exactly one required database secret."
}

resource "azurerm_role_assignment" "policy_index_acr_pull" {
  count = local.policy_index_enabled ? 1 : 0

  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.policy_index[0].principal_id
  principal_type       = "ServicePrincipal"
  description          = "Allow only the policy indexing identity to pull its immutable image."
}

resource "azurerm_role_assignment" "policy_index_key_vault_secret" {
  count = local.policy_index_enabled ? 1 : 0

  scope              = "${azurerm_key_vault.main.id}/secrets/${coalesce(local.postgresql_application_secret_name, "not-configured")}"
  role_definition_id = local.key_vault_secrets_user_role_definition_id
  principal_id       = azurerm_user_assigned_identity.policy_index[0].principal_id
  principal_type     = "ServicePrincipal"
  description        = "Allow the policy indexing identity to read only the application database secret."
}
