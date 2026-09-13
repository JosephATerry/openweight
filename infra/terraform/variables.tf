variable "location" {
  description = "Azure region selected after service-availability, cost, residency, and latency review."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]+$", var.location))
    error_message = "location must be a lowercase Azure location identifier such as eastus."
  }
}

variable "region_code" {
  description = "Short deterministic code used only in resource names."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{2,8}$", var.region_code))
    error_message = "region_code must contain 2-8 lowercase letters or digits."
  }
}

variable "environment" {
  description = "Deployment environment name. Use a separate state and resource boundary per environment."
  type        = string
  default     = "demo"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,10}[a-z0-9]$", var.environment))
    error_message = "environment must be 3-12 lowercase letters, digits, or interior hyphens."
  }
}

variable "project_name" {
  description = "Short project prefix used in deterministic resource names and tags."
  type        = string
  default     = "owp"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,18}[a-z0-9]$", var.project_name))
    error_message = "project_name must be 3-20 lowercase letters, digits, or interior hyphens."
  }
}

variable "unique_suffix" {
  description = "Caller-selected suffix for globally unique Azure names; it is not generated or reserved by D13."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{4,12}$", var.unique_suffix))
    error_message = "unique_suffix must contain 4-12 lowercase letters or digits."
  }
}

variable "additional_tags" {
  description = "Additional non-sensitive resource tags."
  type        = map(string)
  default     = {}
}

variable "deployment_stage" {
  description = "Explicit lifecycle stage: foundation, first bootstrap, application, or later maintenance with app plus manual jobs."
  type        = string
  default     = "foundation"

  validation {
    condition     = contains(["foundation", "bootstrap", "application", "maintenance"], var.deployment_stage)
    error_message = "deployment_stage must be foundation, bootstrap, application, or maintenance."
  }
}

variable "container_image" {
  description = "Immutable application image reference. Required only for bootstrap and application stages."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.container_image == null || can(regex("@sha256:[0-9a-fA-F]{64}$", var.container_image))
    error_message = "container_image must be pinned by a 64-character sha256 digest."
  }
}

variable "container_port" {
  description = "FastAPI container and ingress target port."
  type        = number
  default     = 8000

  validation {
    condition     = var.container_port >= 1024 && var.container_port <= 65535
    error_message = "container_port must be between 1024 and 65535."
  }
}

variable "container_app_external_ingress_enabled" {
  description = "Expose the API through Container Apps managed HTTPS ingress; set false for an internal-only deployment."
  type        = bool
  default     = true
}

variable "container_app_allowed_ingress_cidrs" {
  description = "Optional source restrictions. Leave empty for the recruiter-facing public HTTPS demo."
  type        = list(string)
  default     = []

  validation {
    condition = (
      alltrue([for cidr in var.container_app_allowed_ingress_cidrs : can(cidrhost(cidr, 0))]) &&
      !contains(var.container_app_allowed_ingress_cidrs, "0.0.0.0/0") &&
      !contains(var.container_app_allowed_ingress_cidrs, "::/0")
    )
    error_message = "Ingress entries must be valid restricted CIDRs; use an empty list for public access."
  }
}

variable "container_cpu" {
  description = "Consumption-profile vCPU allocation. Keep paired with a supported memory value."
  type        = number
  default     = 1.0

  validation {
    condition     = contains([0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0], var.container_cpu)
    error_message = "container_cpu must use a supported Container Apps consumption allocation."
  }
}

variable "container_memory" {
  description = "Consumption-profile memory allocation paired with container_cpu."
  type        = string
  default     = "2Gi"

  validation {
    condition     = contains(["0.5Gi", "1Gi", "1.5Gi", "2Gi", "2.5Gi", "3Gi", "3.5Gi", "4Gi"], var.container_memory)
    error_message = "container_memory must use a supported Container Apps consumption allocation."
  }
}

variable "container_app_min_replicas" {
  description = "Minimum HTTP replicas. Zero enables the cost-conscious portfolio app to scale down while durable state remains in PostgreSQL."
  type        = number
  default     = 0

  validation {
    condition     = contains([0, 1], var.container_app_min_replicas)
    error_message = "container_app_min_replicas must be zero or one."
  }
}

variable "container_app_max_replicas" {
  description = "Conservative maximum. D14 adds durability, but deployment verification remains required before horizontal scaling."
  type        = number
  default     = 1

  validation {
    condition     = var.container_app_max_replicas == 1
    error_message = "The reference deployment remains single-replica until durable production bootstrap and load validation are complete."
  }
}

variable "product_auth_enabled" {
  description = "Require validated OIDC bearer tokens for product routes. Cloud deployments keep this enabled."
  type        = bool
  default     = true
}

variable "public_read_enabled" {
  description = "Permit anonymous read/query routes while governed write routes continue to require Entra authorization."
  type        = bool
  default     = true
}

variable "auth_issuer" {
  description = "HTTPS OIDC issuer, normally the Microsoft Entra tenant v2 issuer."
  type        = string

  validation {
    condition     = !var.product_auth_enabled || can(regex("^https://", var.auth_issuer))
    error_message = "auth_issuer must be HTTPS when product authentication is enabled."
  }
}

variable "auth_audience" {
  description = "Application/API audience expected in access tokens; not a secret."
  type        = string

  validation {
    condition     = !var.product_auth_enabled || length(trimspace(var.auth_audience)) > 0
    error_message = "auth_audience is required when product authentication is enabled."
  }
}

variable "auth_jwks_url" {
  description = "HTTPS JWKS endpoint used to validate access-token signatures."
  type        = string

  validation {
    condition     = !var.product_auth_enabled || can(regex("^https://", var.auth_jwks_url))
    error_message = "auth_jwks_url must be HTTPS when product authentication is enabled."
  }
}

variable "github_federation_enabled" {
  description = "Create the GitHub Environment-bound OIDC federated credential."
  type        = bool
  default     = false
}

variable "github_repository_owner" {
  description = "Canonical GitHub repository owner trusted by federation."
  type        = string
  default     = "JosephATerry"
}

variable "github_repository" {
  description = "Canonical GitHub repository trusted by federation."
  type        = string
  default     = "openweight"
}

variable "github_branch" {
  description = "Branch trusted by the future federated credential when no GitHub environment is used."
  type        = string
  default     = "main"
}

variable "github_environment" {
  description = "Protected GitHub environment used in the OIDC subject."
  type        = string
  default     = "azure-production"
}

variable "application_version" {
  description = "Safe application version metadata exposed by the service."
  type        = string
  default     = "0.1.0"
}

variable "log_level" {
  description = "Structured application log level."
  type        = string
  default     = "INFO"

  validation {
    condition     = contains(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], var.log_level)
    error_message = "log_level must be a standard uppercase Python log level."
  }
}

variable "backend_alias" {
  description = "Existing GPT-OSS backend alias used with Hugging Face routed inference."
  type        = string
  default     = "gpt-oss"

  validation {
    condition     = var.backend_alias == "gpt-oss"
    error_message = "Azure routed inference requires the existing gpt-oss backend."
  }
}

variable "huggingface_provider" {
  description = "Explicit Hugging Face routed inference provider."
  type        = string
  default     = "groq"

  validation {
    condition     = var.huggingface_provider == "groq"
    error_message = "The approved Azure provider is groq."
  }
}

variable "gpt_oss_model_id" {
  description = "Exact model routed through Hugging Face Inference Providers."
  type        = string
  default     = "openai/gpt-oss-20b"

  validation {
    condition     = var.gpt_oss_model_id == "openai/gpt-oss-20b"
    error_message = "The approved Azure model is openai/gpt-oss-20b."
  }
}

variable "vnet_address_space" {
  description = "Address spaces for the environment virtual network."
  type        = list(string)
  default     = ["10.40.0.0/16"]

  validation {
    condition     = length(var.vnet_address_space) > 0 && alltrue([for cidr in var.vnet_address_space : can(cidrhost(cidr, 0))])
    error_message = "Every vnet_address_space entry must be a valid CIDR."
  }
}

variable "container_apps_subnet_cidr" {
  description = "Dedicated Container Apps infrastructure subnet; consumption-only environments require /23 or larger."
  type        = string
  default     = "10.40.0.0/23"

  validation {
    condition = (
      can(cidrhost(var.container_apps_subnet_cidr, 0)) &&
      try(tonumber(split("/", var.container_apps_subnet_cidr)[1]), 99) <= 23
    )
    error_message = "container_apps_subnet_cidr must be valid and /23 or larger."
  }
}

variable "postgresql_subnet_cidr" {
  description = "Dedicated delegated subnet for PostgreSQL Flexible Server."
  type        = string
  default     = "10.40.2.0/24"

  validation {
    condition     = can(cidrhost(var.postgresql_subnet_cidr, 0))
    error_message = "postgresql_subnet_cidr must be a valid CIDR."
  }
}

variable "postgresql_version" {
  description = "PostgreSQL major version verified for pgvector availability in the selected Azure region."
  type        = string
  default     = "17"

  validation {
    condition     = contains(["14", "15", "16", "17", "18"], var.postgresql_version)
    error_message = "postgresql_version must be a currently supported non-extended-support version."
  }
}

variable "postgresql_minimum_tls_version" {
  description = "Minimum TLS protocol accepted by PostgreSQL Flexible Server."
  type        = string
  default     = "TLSv1.2"

  validation {
    condition     = contains(["TLSv1.2", "TLSv1.3"], var.postgresql_minimum_tls_version)
    error_message = "postgresql_minimum_tls_version must be TLSv1.2 or TLSv1.3."
  }
}

variable "postgresql_sku_name" {
  description = "Flexible Server SKU; the demo default is deliberately modest."
  type        = string
  default     = "B_Standard_B1ms"
}

variable "postgresql_storage_mb" {
  description = "Flexible Server storage size in MiB. Azure storage can grow but cannot be reduced in place."
  type        = number
  default     = 32768

  validation {
    condition     = contains([32768, 65536, 131072, 262144, 524288, 1048576, 2097152, 4194304, 8388608, 16777216, 33553408], var.postgresql_storage_mb)
    error_message = "postgresql_storage_mb must be an Azure-supported Flexible Server storage size."
  }
}

variable "postgresql_storage_tier" {
  description = "Flexible Server storage performance tier."
  type        = string
  default     = "P4"

  validation {
    condition     = contains(["P4", "P6", "P10", "P15", "P20", "P30", "P40", "P50", "P60", "P70", "P80"], var.postgresql_storage_tier)
    error_message = "postgresql_storage_tier must be a supported Azure storage tier."
  }
}

variable "postgresql_backup_retention_days" {
  description = "Point-in-time backup retention."
  type        = number
  default     = 7

  validation {
    condition     = var.postgresql_backup_retention_days >= 7 && var.postgresql_backup_retention_days <= 35
    error_message = "postgresql_backup_retention_days must be between 7 and 35."
  }
}

variable "postgresql_geo_redundant_backup_enabled" {
  description = "Enable geo-redundant backup after an explicit cost/recovery review."
  type        = bool
  default     = false
}

variable "postgresql_high_availability_enabled" {
  description = "Enable zone-redundant HA after choosing a compatible non-burstable SKU and region."
  type        = bool
  default     = false
}

variable "postgresql_zone" {
  description = "Optional primary availability zone. Leave null for Azure assignment."
  type        = string
  default     = null

  validation {
    condition     = var.postgresql_zone == null || contains(["1", "2", "3"], var.postgresql_zone)
    error_message = "postgresql_zone must be null, 1, 2, or 3."
  }
}

variable "postgresql_database_name" {
  description = "Application database created on the Flexible Server."
  type        = string
  default     = "openweight_platform"

  validation {
    condition     = can(regex("^[a-z][a-z0-9_]{2,62}$", var.postgresql_database_name))
    error_message = "postgresql_database_name must be a lowercase PostgreSQL identifier."
  }
}

variable "postgresql_administrator_login" {
  description = "Bootstrap-only database administrator login; the API must use a separate D14 role."
  type        = string
  default     = "owp_tf_admin"
}

variable "postgresql_administrator_password" {
  description = "Out-of-band bootstrap password. Ephemeral and passed only to the provider write-only argument."
  type        = string
  sensitive   = true
  ephemeral   = true

  validation {
    condition     = length(var.postgresql_administrator_password) >= 16
    error_message = "postgresql_administrator_password must contain at least 16 characters."
  }
}

variable "postgresql_administrator_password_version" {
  description = "Increment to rotate the provider write-only administrator password."
  type        = number
  default     = 1

  validation {
    condition     = var.postgresql_administrator_password_version >= 1
    error_message = "postgresql_administrator_password_version must be at least 1."
  }
}

variable "postgresql_application_login" {
  description = "Least-privilege runtime role created later by the D14 migration/bootstrap lifecycle."
  type        = string
  default     = "openweight_app"
}

variable "postgresql_application_password_secret_id" {
  description = "Versionless Key Vault secret URI created out of band in D14; this is a reference, not a secret value."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.postgresql_application_password_secret_id == null || can(regex("^https://[a-z0-9-]+\\.vault\\.azure\\.net/secrets/[A-Za-z0-9-]+$", var.postgresql_application_password_secret_id))
    error_message = "postgresql_application_password_secret_id must be a versionless Azure Key Vault secret URI."
  }
}

variable "postgresql_administrator_password_secret_id" {
  description = "Versionless Key Vault URI used only by the temporary bootstrap jobs."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.postgresql_administrator_password_secret_id == null || can(regex("^https://[a-z0-9-]+\\.vault\\.azure\\.net/secrets/[A-Za-z0-9-]+$", var.postgresql_administrator_password_secret_id))
    error_message = "postgresql_administrator_password_secret_id must be a versionless Azure Key Vault secret URI."
  }
}

variable "huggingface_token_secret_id" {
  description = "Versionless URI for the separately revocable Azure inference-only HF token; Terraform never creates its value."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.huggingface_token_secret_id == null || can(regex("^https://[a-z0-9-]+\\.vault\\.azure\\.net/secrets/[A-Za-z0-9-]+$", var.huggingface_token_secret_id))
    error_message = "huggingface_token_secret_id must be a versionless Azure Key Vault secret URI."
  }
}

variable "log_analytics_retention_days" {
  description = "Log Analytics retention bounded for the portfolio environment."
  type        = number
  default     = 30

  validation {
    condition     = var.log_analytics_retention_days >= 30 && var.log_analytics_retention_days <= 730
    error_message = "log_analytics_retention_days must be between 30 and 730."
  }
}

variable "log_analytics_daily_quota_gb" {
  description = "Daily Log Analytics ingestion cap in GiB."
  type        = number
  default     = 1

  validation {
    condition     = var.log_analytics_daily_quota_gb > 0
    error_message = "log_analytics_daily_quota_gb must be positive."
  }
}
