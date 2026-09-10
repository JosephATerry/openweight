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


def test_registry_is_basic_non_admin_and_runtime_is_pull_only() -> None:
    registry = _read("registry.tf")
    identities = _read("identities.tf")
    assert 'sku                           = "Basic"' in registry
    assert "admin_enabled                 = false" in registry
    assert "anonymous_pull_enabled        = true" not in registry
    assert 'role_definition_name = "AcrPull"' in identities
    assert 'resource "azurerm_role_assignment" "runtime_acr_pull"' in identities
    assert 'resource "azurerm_role_assignment" "ci_acr_push"' in identities
    assert 'role_definition_name = "AcrPush"' in identities


def test_private_postgresql_network_and_pgvector_intent() -> None:
    networking = _read("networking.tf")
    postgres = _read("postgres.tf")
    assert 'name = "Microsoft.DBforPostgreSQL/flexibleServers"' in networking
    assert "private_dns_zone_id" in postgres
    assert "public_network_access_enabled = false" in postgres
    assert 'name      = "azure.extensions"' in postgres
    assert 'value     = "vector"' in postgres
    assert "CREATE EXTENSION vector" in postgres
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


def test_container_app_uses_identity_probes_and_one_replica() -> None:
    app = _read("container_apps.tf")
    variables = _read("variables.tf")
    assert app.count('resource "azurerm_container_app"') == 1
    assert 'type         = "UserAssigned"' in app
    assert 'path                    = "/healthz"' in app
    assert 'path                    = "/readyz"' in app
    assert 'name  = "OPENWEIGHT_METRICS_ENABLED"' in app
    assert re.search(r'OPENWEIGHT_METRICS_ENABLED"\s+value = "false"', app)
    assert 'variable "container_app_min_replicas"' in variables
    assert 'variable "container_app_max_replicas"' in variables
    assert variables.count("condition     = var.container_app_") == 2


def test_external_ingress_has_explicit_restricted_source_contract() -> None:
    app = _read("container_apps.tf")
    variables = _read("variables.tf")
    example = _read("terraform.tfvars.example")
    assert "container_app_external_ingress_enabled" in variables
    assert "container_app_allowed_ingress_cidrs" in variables
    assert '"0.0.0.0/0"' in variables
    assert '"::/0"' in variables
    assert 'dynamic "ip_security_restriction"' in app
    assert 'action           = "Allow"' in app
    assert 'container_app_allowed_ingress_cidrs = ["198.51.100.0/24"]' in example


def test_observability_is_workspace_based_and_bounded() -> None:
    observability = _read("observability.tf")
    assert re.search(r'sku\s*=\s*"PerGB2018"', observability)
    assert "daily_quota_gb" in observability
    assert "workspace_id" in observability
    assert "sampling_percentage" in observability
    assert "ip_masking_enabled" in observability


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
    assert 'value = "change-me' not in _all_terraform().lower()


def test_example_tfvars_contains_no_secret_value() -> None:
    example = _read("terraform.tfvars.example")
    assert "postgresql_administrator_password =" not in example
    assert "api_key" not in example.lower()
    assert "client_secret" not in example.lower()
    assert "password_secret_id" in example
    assert "example.invalid" in example


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
