locals {
  bootstrap_jobs = local.bootstrap_enabled ? {
    migrate = {
      name    = local.migration_job_name
      command = ["python", "scripts/migrate_database.py", "--seed-demo-data"]
      timeout = 900
    }
    policy-index = {
      name    = local.policy_index_job_name
      command = ["python", "scripts/index_policy_corpus.py"]
      timeout = 1800
    }
  } : {}
}

resource "azurerm_container_app_job" "database_bootstrap" {
  for_each = local.bootstrap_jobs

  name                         = each.value.name
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  replica_timeout_in_seconds   = each.value.timeout
  replica_retry_limit          = 0
  tags                         = local.common_tags

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.bootstrap[0].id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.bootstrap[0].id
  }

  secret {
    name                = "postgres-admin-password"
    identity            = azurerm_user_assigned_identity.bootstrap[0].id
    key_vault_secret_id = var.postgresql_administrator_password_secret_id
  }

  secret {
    name                = "postgres-application-password"
    identity            = azurerm_user_assigned_identity.bootstrap[0].id
    key_vault_secret_id = var.postgresql_application_password_secret_id
  }

  template {
    container {
      name    = each.key
      image   = coalesce(var.container_image, "invalid.invalid/not-configured@sha256:0000000000000000000000000000000000000000000000000000000000000000")
      cpu     = 1
      memory  = "2Gi"
      command = each.value.command

      env {
        name  = "POSTGRES_DB"
        value = azurerm_postgresql_flexible_server_database.application.name
      }

      env {
        name  = "POSTGRES_USER"
        value = var.postgresql_administrator_login
      }

      env {
        name        = "POSTGRES_PASSWORD"
        secret_name = "postgres-admin-password" # pragma: allowlist secret
      }

      env {
        name  = "POSTGRES_HOST"
        value = azurerm_postgresql_flexible_server.main.fqdn
      }

      env {
        name  = "POSTGRES_PORT"
        value = "5432"
      }

      env {
        name  = "POSTGRES_SSLMODE"
        value = "verify-full"
      }

      env {
        name  = "POSTGRES_SSLROOTCERT"
        value = "system"
      }

      env {
        name  = "POSTGRES_APPLICATION_ROLE"
        value = var.postgresql_application_login
      }

      env {
        name        = "POSTGRES_APPLICATION_PASSWORD"
        secret_name = "postgres-application-password" # pragma: allowlist secret
      }

      env {
        name  = "OPENWEIGHT_DEMO_EMBEDDING_MODEL"
        value = "/app/models/qwen3-embedding-0.6b"
      }
    }
  }

  depends_on = [
    azurerm_postgresql_flexible_server_configuration.extensions,
    azurerm_role_assignment.bootstrap_acr_pull,
    azurerm_role_assignment.bootstrap_key_vault_secrets,
  ]
}
