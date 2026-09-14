locals {
  name_base = substr("${var.project_name}-${var.environment}", 0, 24)

  resource_group_name         = "rg-${local.name_base}-${var.region_code}"
  virtual_network_name        = "vnet-${local.name_base}-${var.region_code}"
  container_apps_subnet_name  = "snet-${local.name_base}-aca"
  postgresql_subnet_name      = "snet-${local.name_base}-postgres"
  container_registry_name     = substr("acr${replace(var.project_name, "-", "")}${replace(var.environment, "-", "")}${var.region_code}${var.unique_suffix}", 0, 50)
  runtime_identity_name       = "id-${local.name_base}-api-${var.region_code}"
  ci_identity_name            = "id-${local.name_base}-ci-${var.region_code}"
  migration_identity_name     = "id-${local.name_base}-migrate-${var.region_code}"
  policy_index_identity_name  = "id-${local.name_base}-index-${var.region_code}"
  log_analytics_name          = "log-${local.name_base}-${var.region_code}"
  key_vault_name              = substr("kv-${var.unique_suffix}-${var.region_code}-${replace(var.project_name, "-", "")}", 0, 24)
  postgresql_server_name      = substr("psql-${var.unique_suffix}-${var.region_code}-${local.name_base}", 0, 63)
  postgresql_private_dns_name = "${local.name_base}.postgres.database.azure.com"
  container_apps_environment  = "cae-${local.name_base}-${var.region_code}"
  container_app_name          = substr("ca-${local.name_base}-api", 0, 32)
  migration_job_name          = substr("caj-${local.name_base}-migrate", 0, 32)
  policy_index_job_name       = substr("caj-${local.name_base}-index", 0, 32)
  migration_enabled           = contains(["migration", "maintenance_migration"], var.deployment_stage)
  policy_index_enabled        = contains(["indexing", "maintenance_indexing"], var.deployment_stage)
  application_enabled         = contains(["application", "maintenance_migration", "maintenance_indexing"], var.deployment_stage)
  image_consumers_enabled     = local.migration_enabled || local.policy_index_enabled || local.application_enabled
  postgresql_application_secret_name = (
    var.postgresql_application_password_secret_id == null ? null :
    element(split("/", var.postgresql_application_password_secret_id), 4)
  )
  postgresql_administrator_secret_name = (
    var.postgresql_administrator_password_secret_id == null ? null :
    element(split("/", var.postgresql_administrator_password_secret_id), 4)
  )
  huggingface_token_secret_name = (
    var.huggingface_token_secret_id == null ? null :
    element(split("/", var.huggingface_token_secret_id), 4)
  )
  key_vault_secrets_user_role_definition_id = join("", [
    "/subscriptions/${data.azurerm_client_config.current.subscription_id}",
    "/providers/Microsoft.Authorization/roleDefinitions/4633458b-17de-408a-b874-0445c86b69e6",
  ])
  github_oidc_subject = var.github_environment == null ? (
    "repo:${var.github_repository_owner}/${var.github_repository}:ref:refs/heads/${var.github_branch}"
    ) : (
    "repo:${var.github_repository_owner}/${var.github_repository}:environment:${var.github_environment}"
  )

  common_tags = merge(
    {
      project     = var.project_name
      environment = var.environment
      managed-by  = "terraform"
      purpose     = "portfolio-reference"
    },
    var.additional_tags,
  )
}
