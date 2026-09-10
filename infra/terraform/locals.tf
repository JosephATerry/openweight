locals {
  name_base = substr("${var.project_name}-${var.environment}", 0, 24)

  resource_group_name         = "rg-${local.name_base}-${var.region_code}"
  virtual_network_name        = "vnet-${local.name_base}-${var.region_code}"
  container_apps_subnet_name  = "snet-${local.name_base}-aca"
  postgresql_subnet_name      = "snet-${local.name_base}-postgres"
  container_registry_name     = substr("acr${replace(var.project_name, "-", "")}${replace(var.environment, "-", "")}${var.unique_suffix}", 0, 50)
  runtime_identity_name       = "id-${local.name_base}-api-${var.region_code}"
  ci_identity_name            = "id-${local.name_base}-ci-${var.region_code}"
  log_analytics_name          = "log-${local.name_base}-${var.region_code}"
  application_insights_name   = "appi-${local.name_base}-${var.region_code}"
  key_vault_name              = substr("kv-${var.unique_suffix}-${local.name_base}", 0, 24)
  postgresql_server_name      = substr("psql-${var.unique_suffix}-${local.name_base}", 0, 63)
  postgresql_private_dns_name = "${local.name_base}.postgres.database.azure.com"
  container_apps_environment  = "cae-${local.name_base}-${var.region_code}"
  container_app_name          = substr("ca-${local.name_base}-api", 0, 32)
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
