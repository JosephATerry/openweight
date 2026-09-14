from __future__ import annotations

import ipaddress
import re
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
    relative_files = [
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*")
        if path.is_file() and ".terraform" not in path.relative_to(ROOT).parts
    ]
    assert not any(any(marker in path for marker in prohibited) for path in relative_files)


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
    assert 'role_definition_name = "Key Vault Secrets User"' in _read("identities.tf")


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
    assert "administrator_password_wo" in postgres
    assert "administrator_password   =" not in postgres
    assert "key_vault_secret_id = var.postgresql_application_password_secret_id" in app
    assert "key_vault_secret_id = var.huggingface_token_secret_id" in app
    assert 'value = "change-me' not in _all_terraform().lower()


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


def test_documentation_preserves_d12_and_d14_boundaries() -> None:
    readme = " ".join(_read("README.md").split())
    required_phrases = (
        "PostgresSaver",
        "exactly one",
        "transactional execution ledger",
        "OPENWEIGHT_METRICS_ENABLED=false",
        "no GPU",
        "GitHub OIDC",
        "did not authenticate to Azure",
    )
    assert all(phrase in readme for phrase in required_phrases)


def test_existing_ci_has_no_azure_deployment_or_terraform_apply() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    lowered = workflow.lower()
    assert "terraform apply" not in lowered
    assert "azure/login" not in lowered
    assert "az login" not in lowered


def test_staged_bootstrap_jobs_are_manual_additive_and_model_free_by_default() -> None:
    jobs = _read("bootstrap_jobs.tf")
    migration = (ROOT / "scripts" / "migrate_database.py").read_text(
        encoding="utf-8"
    )

    assert 'contains(["bootstrap", "maintenance"], var.deployment_stage)' in _read(
        "locals.tf"
    )
    assert 'manual_trigger_config {' in jobs
    assert 'replica_retry_limit          = 0' in jobs
    assert "migrate_database.py" in jobs
    assert "index_policy_corpus.py" in jobs
    assert "CREATE EXTENSION IF NOT EXISTS vector" in migration
    assert "schema_migrations" in migration
    assert "pg_advisory_lock" in migration
    assert "PostgresSaver" in migration
    assert "generate" not in migration


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
