check "container_consumption_allocation" {
  assert {
    condition = contains(
      [
        "0.25:0.5Gi",
        "0.5:1Gi",
        "0.75:1.5Gi",
        "1:2Gi",
        "1.25:2.5Gi",
        "1.5:3Gi",
        "1.75:3.5Gi",
        "2:4Gi",
      ],
      "${var.container_cpu}:${var.container_memory}",
    )
    error_message = "container_cpu and container_memory must form a supported Consumption allocation."
  }
}

check "image_comes_from_stack_registry" {
  assert {
    condition = !local.image_consumers_enabled || try(
      startswith(lower(var.container_image), "${local.container_registry_name}.azurecr.io/"),
      false,
    )
    error_message = "Migration, indexing, and application stages require an ACR image pinned by digest."
  }
}

check "runtime_secret_comes_from_stack_vault" {
  assert {
    condition = !local.application_enabled || (
      try(startswith(lower(var.postgresql_application_password_secret_id), "https://${local.key_vault_name}.vault.azure.net/secrets/"), false) &&
      local.postgresql_application_secret_name == "postgres-application-password" && # pragma: allowlist secret
      try(startswith(lower(var.huggingface_token_secret_id), "https://${local.key_vault_name}.vault.azure.net/secrets/"), false)
    )
    error_message = "The application stage requires PostgreSQL and HF secret references from this stack's Key Vault."
  }
}

check "migration_secrets_come_from_stack_vault" {
  assert {
    condition = !local.migration_enabled || (
      try(startswith(lower(var.postgresql_administrator_password_secret_id), "https://${local.key_vault_name}.vault.azure.net/secrets/"), false) &&
      try(startswith(lower(var.postgresql_application_password_secret_id), "https://${local.key_vault_name}.vault.azure.net/secrets/"), false) &&
      local.postgresql_administrator_secret_name == "postgres-admin-password" &&  # pragma: allowlist secret
      local.postgresql_application_secret_name == "postgres-application-password" # pragma: allowlist secret
    )
    error_message = "The migration stage requires both database secret references from this stack's Key Vault."
  }
}

check "policy_index_secret_comes_from_stack_vault" {
  assert {
    condition = !local.policy_index_enabled || (
      try(
        startswith(lower(var.postgresql_application_password_secret_id), "https://${local.key_vault_name}.vault.azure.net/secrets/"),
        false,
      ) && local.postgresql_application_secret_name == "postgres-application-password" # pragma: allowlist secret
    )
    error_message = "The indexing stage requires only the application database secret reference from this stack's Key Vault."
  }
}

check "postgresql_ha_uses_non_burstable_sku" {
  assert {
    condition     = !var.postgresql_high_availability_enabled || !startswith(var.postgresql_sku_name, "B_")
    error_message = "PostgreSQL high availability requires a compatible non-burstable SKU."
  }
}

check "github_federation_uses_real_trust_values" {
  assert {
    condition = !var.github_federation_enabled || (
      var.github_repository_owner != "placeholder-owner" &&
      var.github_repository != "placeholder-repository" &&
      length(trimspace(var.github_repository_owner)) > 0 &&
      length(trimspace(var.github_repository)) > 0 &&
      var.github_environment != null &&
      length(trimspace(var.github_environment)) > 0
    )
    error_message = "Enable GitHub federation only after supplying the real repository owner and name."
  }
}
