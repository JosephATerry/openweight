resource "azurerm_container_app_environment" "main" {
  name                           = local.container_apps_environment
  location                       = azurerm_resource_group.main.location
  resource_group_name            = azurerm_resource_group.main.name
  infrastructure_subnet_id       = azurerm_subnet.container_apps.id
  internal_load_balancer_enabled = false
  public_network_access          = "Enabled"
  logs_destination               = "log-analytics"
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.main.id
  tags                           = local.common_tags

  # Azure returns its service-managed zero-count Consumption profile even when
  # configuration omits it. AzureRM 5.3.0 otherwise plans removal on every
  # refresh; ignore only that API-normalized block. This stack defines no
  # non-Consumption profile.
  lifecycle {
    ignore_changes = [workload_profile]
  }
}

resource "azurerm_container_app" "api" {
  count = local.application_enabled ? 1 : 0

  name                         = local.container_app_name
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  max_inactive_revisions       = 3
  workload_profile_name        = "Consumption"
  tags                         = local.common_tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.runtime.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.runtime.id
  }

  secret {
    name                = "postgres-application-password"
    identity            = azurerm_user_assigned_identity.runtime.id
    key_vault_secret_id = var.postgresql_application_password_secret_id
  }

  secret {
    name                = "huggingface-inference-token"
    identity            = azurerm_user_assigned_identity.runtime.id
    key_vault_secret_id = var.huggingface_token_secret_id
  }

  ingress {
    external_enabled           = var.container_app_external_ingress_enabled
    allow_insecure_connections = false
    target_port                = var.container_port
    transport                  = "auto"

    dynamic "ip_security_restriction" {
      for_each = {
        for index, cidr in var.container_app_allowed_ingress_cidrs :
        format("allow-%03d", index + 1) => cidr
      }

      content {
        name             = ip_security_restriction.key
        action           = "Allow"
        ip_address_range = ip_security_restriction.value
        description      = "Caller-approved ingress range."
      }
    }

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas                     = var.container_app_min_replicas
    max_replicas                     = var.container_app_max_replicas
    termination_grace_period_seconds = 30

    container {
      name   = "api"
      image  = coalesce(var.container_image, "invalid.invalid/not-configured@sha256:0000000000000000000000000000000000000000000000000000000000000000")
      cpu    = var.container_cpu
      memory = var.container_memory

      env {
        name  = "OPENWEIGHT_ENVIRONMENT"
        value = var.environment
      }

      env {
        name  = "OPENWEIGHT_SERVICE_VERSION"
        value = var.application_version
      }

      env {
        name  = "OPENWEIGHT_LOG_LEVEL"
        value = var.log_level
      }

      env {
        name  = "OPENWEIGHT_API_HOST"
        value = "0.0.0.0"
      }

      env {
        name  = "OPENWEIGHT_API_PORT"
        value = tostring(var.container_port)
      }

      env {
        name  = "OPENWEIGHT_BACKEND"
        value = var.backend_alias
      }

      env {
        name  = "OPENWEIGHT_DEPLOYMENT_PROFILE"
        value = "azure"
      }

      env {
        name  = "OPENWEIGHT_GPT_OSS_MODEL_ID"
        value = var.gpt_oss_model_id
      }

      env {
        name  = "OPENWEIGHT_HF_PROVIDER"
        value = var.huggingface_provider
      }

      env {
        name  = "OPENWEIGHT_HF_MAX_RETRIES"
        value = "0"
      }

      env {
        name  = "OPENWEIGHT_HF_TIMEOUT_SECONDS"
        value = "60"
      }

      env {
        name  = "OPENWEIGHT_HF_INFERENCE_CONCURRENCY_LIMIT"
        value = "2"
      }

      env {
        name  = "OPENWEIGHT_HF_RATE_LIMIT_REQUESTS"
        value = "5"
      }

      env {
        name  = "OPENWEIGHT_HF_RATE_LIMIT_WINDOW_SECONDS"
        value = "60"
      }

      env {
        name  = "OPENWEIGHT_HF_RATE_LIMIT_MAX_CLIENTS"
        value = "1024"
      }

      env {
        name  = "OPENWEIGHT_DEMO_EMBEDDING_MODEL"
        value = "/app/models/qwen3-embedding-0.6b"
      }

      env {
        name        = "HF_TOKEN"
        secret_name = "huggingface-inference-token" # pragma: allowlist secret
      }

      env {
        name  = "OPENWEIGHT_DATABASE_REQUIRED"
        value = "true"
      }

      env {
        name  = "OPENWEIGHT_DATABASE_PROBE_TIMEOUT_SECONDS"
        value = "2"
      }

      env {
        name  = "OPENWEIGHT_CHECKPOINT_BACKEND"
        value = "postgres"
      }

      env {
        name  = "OPENWEIGHT_WEB_ENABLED"
        value = "false"
      }

      env {
        name  = "OPENWEIGHT_FRONTEND_ENABLED"
        value = "true"
      }

      env {
        name  = "OPENWEIGHT_REASONING_STRENGTH"
        value = "low"
      }

      env {
        name  = "OPENWEIGHT_OBSERVABILITY_ENABLED"
        value = "true"
      }

      env {
        name  = "OPENWEIGHT_METRICS_ENABLED"
        value = "false"
      }

      env {
        name  = "OPENWEIGHT_METRICS_ACCESS_MODE"
        value = "disabled"
      }

      env {
        name  = "OPENWEIGHT_TRACING_ENABLED"
        value = "false"
      }

      env {
        name  = "OPENWEIGHT_OBSERVABILITY_SERVICE_NAME"
        value = "openweight-platform"
      }

      env {
        name  = "OPENWEIGHT_AUTH_ENABLED"
        value = tostring(var.product_auth_enabled)
      }

      env {
        name  = "OPENWEIGHT_PUBLIC_READ_ENABLED"
        value = tostring(var.public_read_enabled)
      }

      env {
        name  = "OPENWEIGHT_CORS_ALLOWED_ORIGINS"
        value = trimsuffix(azurerm_storage_account.frontend.primary_web_endpoint, "/")
      }

      env {
        name  = "OPENWEIGHT_AUTH_ISSUER"
        value = var.auth_issuer
      }

      env {
        name  = "OPENWEIGHT_AUTH_AUDIENCE"
        value = var.auth_audience
      }

      env {
        name  = "OPENWEIGHT_AUTH_JWKS_URL"
        value = var.auth_jwks_url
      }

      env {
        name  = "LANGGRAPH_STRICT_MSGPACK"
        value = "true"
      }

      env {
        name  = "POSTGRES_DB"
        value = azurerm_postgresql_flexible_server_database.application.name
      }

      env {
        name  = "POSTGRES_USER"
        value = var.postgresql_application_login
      }

      env {
        name        = "POSTGRES_PASSWORD"
        secret_name = "postgres-application-password" # pragma: allowlist secret
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

      liveness_probe {
        transport               = "HTTP"
        port                    = var.container_port
        path                    = "/healthz"
        initial_delay           = 10
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }

      readiness_probe {
        transport               = "HTTP"
        port                    = var.container_port
        path                    = "/readyz"
        initial_delay           = 10
        interval_seconds        = 15
        timeout                 = 5
        failure_count_threshold = 3
        success_count_threshold = 1
      }
    }
  }

  depends_on = [
    azurerm_role_assignment.runtime_acr_pull,
    azurerm_role_assignment.runtime_key_vault_secrets,
    azurerm_postgresql_flexible_server_configuration.extensions,
    azapi_resource.frontend_static_website,
  ]

  lifecycle {
    # Steady-state CD owns only the immutable image field. Terraform continues
    # to own configuration, identities, secrets, ingress, and scaling.
    ignore_changes = [template[0].container[0].image]
  }
}
