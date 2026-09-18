from __future__ import annotations

import ipaddress
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TF_ROOT = ROOT / "infra" / "terraform"


def _read(name: str) -> str:
    return (TF_ROOT / name).read_text(encoding="utf-8")


def _all_terraform() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(TF_ROOT.glob("*.tf")))


def test_expected_terraform_root_is_complete() -> None:
    expected = {
        ".terraform.lock.hcl",
        "README.md",
        "backend.tf",
        "bootstrap_jobs.tf",
        "checks.tf",
        "container_apps.tf",
        "frontend.tf",
        "identities.tf",
        "key_vault.tf",
        "locals.tf",
        "networking.tf",
        "observability.tf",
        "outputs.tf",
        "postgres.tf",
        "providers.tf",
        "registry.tf",
        "resource_group.tf",
        "terraform.tfvars.example",
        "variables.tf",
        "versions.tf",
    }
    assert {path.name for path in TF_ROOT.iterdir() if path.is_file()} == expected


def test_remote_backend_and_supported_provider_are_declared() -> None:
    assert 'backend "azurerm" {}' in _read("backend.tf")
    versions = _read("versions.tf")
    assert 'source  = "hashicorp/azurerm"' in versions
    assert 'version = "~> 5.0"' in versions
    assert ">= 1.11.0" in versions


def test_no_state_plan_or_private_override_is_committed() -> None:
    prohibited = (
        ".tfstate",
        ".tfplan",
        "override.tf",
        "backend.hcl",
    )
    tracked_files = subprocess.run(
        ["git", "ls-files", "infra/terraform"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert not any(any(marker in path for marker in prohibited) for path in tracked_files)


def test_no_aks_gpu_or_terraform_provisioner_exists() -> None:
    terraform = _all_terraform().lower()
    assert "azurerm_kubernetes_cluster" not in terraform
    assert "gpu" not in terraform
    assert 'provisioner "' not in terraform
    assert "local-exec" not in terraform
    assert "remote-exec" not in terraform


def test_registry_uses_free_account_standard_non_admin_and_pull_only_runtime() -> None:
    registry = _read("registry.tf")
    identities = _read("identities.tf")
    assert 'sku                           = "Standard"' in registry
    assert "admin_enabled                 = false" in registry
    assert "anonymous_pull_enabled        = true" not in registry
    assert 'role_definition_name = "AcrPull"' in identities
    assert 'resource "azurerm_role_assignment" "runtime_acr_pull"' in identities
    assert 'resource "azurerm_role_assignment" "ci_acr_push"' in identities
    assert 'role_definition_name = "AcrPush"' in identities


def test_static_frontend_is_dedicated_keyless_and_narrowly_publishable() -> None:
    frontend = _read("frontend.tf")
    app = _read("container_apps.tf")
    outputs = _read("outputs.tf")

    assert 'resource "azurerm_storage_account" "frontend"' in frontend
    assert 'resource "azurerm_storage_account_static_website" "frontend"' in frontend
    assert 'index_document     = "index.html"' in frontend
    assert re.search(
        r"^\s*shared_access_key_enabled\s*=\s*false\s*$",
        frontend,
        flags=re.MULTILINE,
    )
    assert re.search(
        r"^\s*default_to_oauth_authentication\s*=\s*true\s*$",
        frontend,
        flags=re.MULTILINE,
    )
    assert 'role_definition_name = "Storage Blob Data Contributor"' in frontend
    assert '/blobServices/default/containers/$web' in frontend
    assert "azurerm_user_assigned_identity.ci.principal_id" in frontend
    assert "primary_access_key" not in frontend.lower()
    assert "secondary_access_key" not in frontend.lower()
    assert "listkeys" not in frontend.lower()
    assert 'name  = "OPENWEIGHT_CORS_ALLOWED_ORIGINS"' in app
    assert "azurerm_storage_account.frontend.primary_web_endpoint" in app
    assert "frontend_static_https_origin" in outputs
    assert "primary_web_endpoint" in outputs


def test_static_frontend_does_not_change_backend_scale_to_zero() -> None:
    variables = _read("variables.tf")
    app = _read("container_apps.tf")

    assert re.search(
        r'variable "container_app_min_replicas".*?default\s+=\s+0',
        variables,
        flags=re.DOTALL,
    )
    assert "min_replicas                     = var.container_app_min_replicas" in app
    assert 'workload_profile_name        = "Consumption"' in app


def test_private_postgresql_network_and_pgvector_intent() -> None:
    networking = _read("networking.tf")
    postgres = _read("postgres.tf")
    migration = (ROOT / "scripts" / "migrate_database.py").read_text(
        encoding="utf-8"
    )
    assert 'name = "Microsoft.DBforPostgreSQL/flexibleServers"' in networking
    assert "private_dns_zone_id" in postgres
    assert "public_network_access_enabled = false" in postgres
    assert 'name      = "azure.extensions"' in postgres
    assert 'value     = "vector"' in postgres
    assert "CREATE EXTENSION IF NOT EXISTS vector" in migration
    assert 'name      = "ssl_min_protocol_version"' in postgres
    assert "postgresql_minimum_tls_version" in postgres


def test_postgresql_subnet_preserves_storage_endpoint_and_delegation() -> None:
    networking = _read("networking.tf")
    postgres_subnet = networking.split(
        'resource "azurerm_subnet" "postgresql"', maxsplit=1
    )[1].split('resource "azurerm_private_dns_zone"', maxsplit=1)[0]

    assert postgres_subnet.count("service_endpoint {") == 1
    assert postgres_subnet.count('service = "Microsoft.Storage"') == 1
    assert 'name = "Microsoft.DBforPostgreSQL/flexibleServers"' in postgres_subnet
    assert "service_endpoints" not in postgres_subnet


def test_consumption_environment_ignores_only_provider_normalized_profile() -> None:
    environment = _read("container_apps.tf").split(
        'resource "azurerm_container_app" "api"', maxsplit=1
    )[0]

    assert "service-managed zero-count Consumption profile" in environment
    assert "ignore_changes = [workload_profile]" in environment
    assert "workload_profile {" not in environment
    assert "Dedicated" not in environment
    assert "GPU" not in environment


def test_default_subnets_are_non_overlapping_children_of_default_vnet() -> None:
    variables = _read("variables.tf")

    def default_for(variable: str) -> str:
        block = re.search(
            rf'variable "{variable}" \{{(?P<body>.*?)\n\}}',
            variables,
            flags=re.DOTALL,
        )
        assert block is not None
        value = re.search(r'default\s*=\s*"(?P<value>[^"]+)"', block.group("body"))
        assert value is not None
        return value.group("value")

    vnet_match = re.search(
        r'variable "vnet_address_space" \{.*?default\s*=\s*\["(?P<value>[^"]+)"\]',
        variables,
        flags=re.DOTALL,
    )
    assert vnet_match is not None
    vnet = ipaddress.ip_network(vnet_match.group("value"))
    app_subnet = ipaddress.ip_network(default_for("container_apps_subnet_cidr"))
    database_subnet = ipaddress.ip_network(default_for("postgresql_subnet_cidr"))
    assert app_subnet.subnet_of(vnet)
    assert database_subnet.subnet_of(vnet)
    assert not app_subnet.overlaps(database_subnet)


def test_key_vault_has_rbac_without_secret_payload_resource() -> None:
    key_vault = _read("key_vault.tf")
    terraform = _all_terraform()
    assert "rbac_authorization_enabled    = true" in key_vault
    assert "purge_protection_enabled      = true" in key_vault
    assert 'resource "azurerm_key_vault_secret"' not in terraform
    identities = _read("identities.tf")
    locals_tf = _read("locals.tf")
    assert "4633458b-17de-408a-b874-0445c86b69e6" in locals_tf
    assert "role_definition_id = local.key_vault_secrets_user_role_definition_id" in identities


def test_container_app_uses_identity_safe_probes_and_scale_to_zero() -> None:
    app = _read("container_apps.tf")
    variables = _read("variables.tf")
    assert app.count('resource "azurerm_container_app"') == 1
    assert "local.application_enabled ? 1 : 0" in app
    assert 'type         = "UserAssigned"' in app
    assert 'path                    = "/healthz"' in app
    assert 'path                    = "/readyz"' in app
    assert 'name  = "OPENWEIGHT_METRICS_ENABLED"' in app
    assert re.search(r'OPENWEIGHT_METRICS_ENABLED"\s+value = "false"', app)
    assert 'variable "container_app_min_replicas"' in variables
    assert 'variable "container_app_max_replicas"' in variables
    assert 'default     = 0' in re.search(
        r'variable "container_app_min_replicas" \{(.*?)\n\}',
        variables,
        flags=re.DOTALL,
    ).group(1)
    assert re.search(r'OPENWEIGHT_FRONTEND_ENABLED"\s+value = "true"', app)
    assert re.search(r'OPENWEIGHT_DEPLOYMENT_PROFILE"\s+value = "azure"', app)
    assert re.search(r'OPENWEIGHT_HF_PROVIDER"\s+value = var.huggingface_provider', app)
    assert re.search(r'OPENWEIGHT_PUBLIC_READ_ENABLED"\s+value = tostring\(var.public_read_enabled\)', app)
    assert "OPENWEIGHT_MUSE_BASE_URL" not in app
    assert "ignore_changes = [template[0].container[0].image]" in app


def test_external_ingress_supports_public_recruiters_or_restricted_posture() -> None:
    app = _read("container_apps.tf")
    variables = _read("variables.tf")
    example = _read("terraform.tfvars.example")
    assert "container_app_external_ingress_enabled" in variables
    assert "container_app_allowed_ingress_cidrs" in variables
    assert "default     = []" in variables
    assert 'dynamic "ip_security_restriction"' in app
    assert 'action           = "Allow"' in app
    assert "container_app_allowed_ingress_cidrs = []" in example


def test_observability_is_log_analytics_only_and_bounded() -> None:
    observability = _read("observability.tf")
    assert re.search(r'sku\s*=\s*"PerGB2018"', observability)
    assert "daily_quota_gb" in observability
    assert "azurerm_application_insights" not in observability


def test_secret_inputs_are_references_or_ephemeral_not_literal_values() -> None:
    variables = _read("variables.tf")
    postgres = _read("postgres.tf")
    app = _read("container_apps.tf")
    assert 'variable "postgresql_administrator_password"' in variables
    assert "sensitive   = true" in variables
    assert "ephemeral   = true" in variables
    assert "default     = null" in variables
    assert 'variable "postgresql_administrator_password_required"' in variables
    assert "var.postgresql_administrator_password_required" in variables
    assert "later stages must leave both disabled/null" in variables
    assert "administrator_password_wo" in postgres
    assert (
        "administrator_password_wo_version = "
        "var.postgresql_administrator_password_required ? "
        "var.postgresql_administrator_password_version : null"
    ) in postgres
    assert "administrator_password   =" not in postgres
    assert "key_vault_secret_id = var.postgresql_application_password_secret_id" in app
    assert "key_vault_secret_id = var.huggingface_token_secret_id" in app
    assert 'value = "change-me' not in _all_terraform().lower()


def test_postgresql_write_only_password_pair_is_explicitly_coupled() -> None:
    variables = _read("variables.tf")
    postgres = _read("postgres.tf")
    password_block = re.search(
        r'variable "postgresql_administrator_password" \{(?P<body>.*?)\n\}',
        variables,
        flags=re.DOTALL,
    )

    assert password_block is not None
    assert "sensitive   = true" in password_block.group("body")
    assert "ephemeral   = true" in password_block.group("body")
    assert "default     = null" in password_block.group("body")
    assert (
        "var.postgresql_administrator_password_required && "
        "var.postgresql_administrator_password != null"
    ) in password_block.group("body")
    assert (
        "!var.postgresql_administrator_password_required && "
        "var.postgresql_administrator_password == null"
    ) in password_block.group("body")
    assert re.search(
        r"administrator_password_wo_version\s*=\s*"
        r"var\.postgresql_administrator_password_required\s*\?\s*"
        r"var\.postgresql_administrator_password_version\s*:\s*null",
        postgres,
    )
    assert "initial foundation password" in password_block.group("body")


def test_postgresql_lifecycle_ignores_only_azure_zone_and_creation_marker() -> None:
    variables = _read("variables.tf")
    postgres = _read("postgres.tf")
    zone_block = re.search(
        r'variable "postgresql_zone" \{(?P<body>.*?)\n\}',
        variables,
        flags=re.DOTALL,
    )
    lifecycle = re.search(
        r"lifecycle \{\s*ignore_changes = \[(?P<body>.*?)\]\s*\}",
        postgres,
        flags=re.DOTALL,
    )

    assert zone_block is not None
    assert 'type        = string' in zone_block.group("body")
    assert 'default     = null' in zone_block.group("body")
    assert 'default     = "3"' not in zone_block.group("body")
    assert lifecycle is not None
    ignored = {
        item.strip().rstrip(",")
        for item in lifecycle.group("body").splitlines()
        if item.strip()
    }
    assert ignored == {"administrator_password_wo_version", "zone"}
    assert "Azure selects the initial primary zone" in postgres
    assert "creation-time state marker" in postgres
    assert 'mode = "ZoneRedundant"' in postgres
    assert "var.postgresql_high_availability_enabled ? [1] : []" in postgres


def test_administrator_password_rotation_contract_is_out_of_band() -> None:
    variables = _read("variables.tf")
    readme = (TF_ROOT / "README.md").read_text(encoding="utf-8")
    postgres = _read("postgres.tf")

    assert "keep false for every post-creation operation" in variables
    assert "Post-creation Terraform ignores this marker" in variables
    assert "Do not\n   increment it to rotate an existing server" in readme
    assert "separately authorized out-of-band Azure operation" in readme
    assert "future server mutation requires a separate credential-aware review" in readme
    assert "administrator_password_wo_version" in postgres
    assert "administrator_password_wo_version," in postgres


def test_example_tfvars_contains_no_secret_value() -> None:
    example = _read("terraform.tfvars.example")
    assert "postgresql_administrator_password =" not in example
    assert "api_key" not in example.lower()
    assert "client_secret" not in example.lower()
    assert "password_secret_id" in example
    assert "example.invalid" in example


def test_central_us_recovery_inputs_and_global_names_are_region_specific() -> None:
    example = _read("terraform.tfvars.example")
    locals_tf = _read("locals.tf")

    assert 'location      = "centralus"' in example
    assert 'region_code   = "cus"' in example
    assert "${var.region_code}${var.unique_suffix}" in locals_tf
    assert '"kv-${var.unique_suffix}-${var.region_code}-' in locals_tf
    assert '"psql-${var.unique_suffix}-${var.region_code}-' in locals_tf
    assert "acrowpdemocusreplace123.azurecr.io" in example
    assert "https://kv-replace123-cus-owp.vault.azure.net/secrets/" in example


def test_documentation_preserves_runtime_and_security_boundaries() -> None:
    readme = " ".join(_read("README.md").split())
    required_phrases = (
        "PostgresSaver",
        "exactly one",
        "transactional execution ledger",
        "OPENWEIGHT_METRICS_ENABLED=false",
        "no GPU",
        "GitHub OIDC",
        "manages the live Azure production infrastructure",
    )
    assert all(phrase in readme for phrase in required_phrases)


def test_existing_ci_has_no_azure_deployment_or_terraform_apply() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    lowered = workflow.lower()
    assert "terraform apply" not in lowered
    assert "azure/login" not in lowered
    assert "az login" not in lowered


def test_staged_migration_and_indexing_jobs_are_separate_and_disabled_by_default() -> None:
    jobs = _read("bootstrap_jobs.tf")
    identities = _read("identities.tf")
    locals_tf = _read("locals.tf")
    migration = (ROOT / "scripts" / "migrate_database.py").read_text(
        encoding="utf-8"
    )

    assert 'default     = "foundation"' in _read("variables.tf")
    assert 'contains(["migration", "maintenance_migration"], var.deployment_stage)' in locals_tf
    assert 'contains(["indexing", "maintenance_indexing"], var.deployment_stage)' in locals_tf
    assert 'contains(["application", "maintenance_migration", "maintenance_indexing"], var.deployment_stage)' in locals_tf
    assert '"maintenance"' not in locals_tf
    assert 'resource "azurerm_container_app_job" "database_migration"' in jobs
    assert 'resource "azurerm_container_app_job" "policy_index"' in jobs
    assert jobs.count('manual_trigger_config {') == 2
    assert jobs.count('replica_retry_limit          = 0') == 2
    assert '["python", "scripts/migrate_database.py", "--seed-demo-data"]' in jobs
    assert '["python", "scripts/index_policy_corpus.py"]' in jobs
    assert "ingress" not in jobs
    assert 'azurerm_user_assigned_identity.migration[0].id' in jobs
    assert 'azurerm_user_assigned_identity.policy_index[0].id' in jobs
    assert 'resource "azurerm_user_assigned_identity" "migration"' in identities
    assert 'resource "azurerm_user_assigned_identity" "policy_index"' in identities
    assert '"id-${local.name_base}-migrate-${var.region_code}"' in locals_tf
    assert '"id-${local.name_base}-index-${var.region_code}"' in locals_tf
    assert 'scope                = azurerm_container_registry.main.id' in identities
    assert identities.count("role_definition_id = local.key_vault_secrets_user_role_definition_id") == 3
    assert "CREATE EXTENSION IF NOT EXISTS vector" in migration
    assert "schema_migrations" in migration
    assert "pg_advisory_lock" in migration
    assert "PostgresSaver" in migration
    assert "generate" not in migration


def test_migration_and_runtime_secret_boundaries_are_non_overlapping() -> None:
    jobs = _read("bootstrap_jobs.tf")
    identities = _read("identities.tf")
    app = _read("container_apps.tf")

    migration_job, policy_job = jobs.split(
        'resource "azurerm_container_app_job" "policy_index"', maxsplit=1
    )
    assert 'key_vault_secret_id = var.postgresql_administrator_password_secret_id' in migration_job
    assert 'key_vault_secret_id = var.postgresql_application_password_secret_id' in migration_job
    assert "postgres-admin-password" not in policy_job
    assert "POSTGRES_APPLICATION_PASSWORD" not in policy_job
    assert 'key_vault_secret_id = var.postgresql_application_password_secret_id' in policy_job
    assert "postgresql_administrator" not in app
    assert 'resource "azurerm_role_assignment" "migration_key_vault_secrets"' in identities
    assert 'resource "azurerm_role_assignment" "policy_index_key_vault_secret"' in identities
    assert "Owner" not in identities
    assert 'role_definition_name = "Contributor"' not in identities


def test_migration_job_uses_private_tls_digest_and_versionless_secret_references() -> None:
    jobs = _read("bootstrap_jobs.tf")
    variables = _read("variables.tf")

    assert 'value = azurerm_postgresql_flexible_server.main.fqdn' in jobs
    assert 'value = "5432"' in jobs
    assert 'value = "verify-full"' in jobs
    assert "POSTGRES_SSLROOTCERT" not in jobs
    assert 'value = azurerm_postgresql_flexible_server_database.application.name' in jobs
    assert 'parallelism              = 1' in jobs
    assert 'replica_completion_count = 1' in jobs
    assert '@sha256:' in jobs
    assert 'can(regex("@sha256:[0-9a-fA-F]{64}$"' in variables
    assert variables.count("versionless Azure Key Vault") >= 2
    assert 'resource "azurerm_key_vault_secret"' not in _all_terraform()


def test_remote_state_bootstrap_is_low_cost_protected_and_separate() -> None:
    state = (TF_ROOT / "state-bootstrap" / "main.tf").read_text(encoding="utf-8")

    assert re.search(r'account_tier\s*=\s*"Standard"', state)
    assert re.search(r'account_replication_type\s*=\s*"LRS"', state)
    assert re.search(r"local_user_enabled\s*=\s*false", state)
    assert re.search(r"shared_access_key_enabled\s*=\s*false", state)
    assert "versioning_enabled  = true" in state
    assert "container_access_type = \"private\"" in state
    assert 'role_definition_name = "Storage Blob Data Contributor"' in state
    assert state.count("prevent_destroy = true") == 3
