output "resource_group_name" {
  description = "Azure resource group containing this environment."
  value       = azurerm_resource_group.main.name
}

output "container_registry_login_server" {
  description = "ACR login server used for immutable image promotion."
  value       = azurerm_container_registry.main.login_server
}

output "container_registry_name" {
  description = "ACR resource name used by deployment automation."
  value       = azurerm_container_registry.main.name
}

output "container_app_name" {
  description = "FastAPI Container App name."
  value       = local.container_app_name
}

output "container_app_fqdn" {
  description = "Managed HTTPS ingress hostname for the API."
  value       = try(azurerm_container_app.api[0].ingress[0].fqdn, null)
}

output "postgresql_server_fqdn" {
  description = "Private PostgreSQL Flexible Server hostname."
  value       = azurerm_postgresql_flexible_server.main.fqdn
}

output "key_vault_name" {
  description = "Key Vault foundation used for future runtime secrets."
  value       = azurerm_key_vault.main.name
}

output "log_analytics_workspace_name" {
  description = "Log Analytics workspace name."
  value       = azurerm_log_analytics_workspace.main.name
}

output "runtime_identity_client_id" {
  description = "Non-secret client ID of the Container App runtime identity."
  value       = azurerm_user_assigned_identity.runtime.client_id
}

output "runtime_identity_principal_id" {
  description = "Non-secret principal ID used for least-privilege role assignments."
  value       = azurerm_user_assigned_identity.runtime.principal_id
}

output "ci_identity_client_id" {
  description = "Non-secret client ID for future GitHub OIDC login."
  value       = azurerm_user_assigned_identity.ci.client_id
}

output "ci_identity_principal_id" {
  description = "Non-secret principal ID of the isolated CI deployment identity."
  value       = azurerm_user_assigned_identity.ci.principal_id
}

output "database_migration_job_name" {
  description = "Manual database migration job name during the migration stage."
  value       = local.migration_job_name
}

output "policy_index_job_name" {
  description = "Manual policy indexing job name during the separate indexing stage."
  value       = local.policy_index_job_name
}
