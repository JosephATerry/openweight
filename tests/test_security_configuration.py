from __future__ import annotations

import re
from pathlib import Path

from openweight_platform.api.config import ServiceSettings
from openweight_platform.api.observability import SAFE_ERROR_TYPES
from openweight_platform.api.security import KNOWN_SCOPES, ROLE_PERMISSIONS
from openweight_platform.security.persistence import SECURITY_SCHEMA_STATEMENTS


ROOT = Path(__file__).resolve().parents[1]
TF_ROOT = ROOT / "infra" / "terraform"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_auth_and_metrics_configuration_defaults_are_local_safe() -> None:
    settings = ServiceSettings.from_env(
        {
            "OPENWEIGHT_ENVIRONMENT": "test",
            "OPENWEIGHT_DATABASE_REQUIRED": "false",
        }
    )

    assert settings.auth_enabled is False
    assert settings.metrics_enabled is True
    assert settings.metrics_access_mode == "public"
    assert settings.checkpoint_backend == "memory"
    assert settings.max_new_tokens == 768
    assert settings.web_enabled is False


def test_production_configuration_requires_auth_metadata_tls_and_private_metrics() -> None:
    settings = ServiceSettings.from_env(
        {
            "OPENWEIGHT_ENVIRONMENT": "production",
            "OPENWEIGHT_AUTH_ENABLED": "true",
            "OPENWEIGHT_DATABASE_REQUIRED": "true",
            "OPENWEIGHT_CHECKPOINT_BACKEND": "postgres",
            "OPENWEIGHT_METRICS_ACCESS_MODE": "public",
            "POSTGRES_DB": "example",
            "POSTGRES_USER": "runtime",
            "POSTGRES_PASSWORD": "placeholder-only",  # pragma: allowlist secret
            "POSTGRES_HOST": "database.example.invalid",
            "POSTGRES_SSLMODE": "require",
        }
    )

    joined = "; ".join(settings.configuration_errors)
    assert "authentication configuration is incomplete" in joined
    assert "sslmode=verify-full" in joined
    assert "metrics cannot use public access mode" in joined


def test_bounded_roles_scopes_and_error_classes() -> None:
    assert set(KNOWN_SCOPES) == {
        "agent.query",
        "actions.propose",
        "approvals.resume",
        "metrics.read",
    }
    assert ROLE_PERMISSIONS["OpenWeight.Reader"] == {"agent.query"}
    assert "approvals.resume" not in ROLE_PERMISSIONS["OpenWeight.Reader"]
    assert "authentication_error" in SAFE_ERROR_TYPES
    assert "authorization_error" in SAFE_ERROR_TYPES


def test_security_schema_is_narrow_and_contains_no_generic_execution_surface() -> None:
    schema = "\n".join(SECURITY_SCHEMA_STATEMENTS).lower()

    assert "approval_sessions" in schema
    assert "execution_ledger" in schema
    assert "access_request_id" in schema
    assert "sql_text" not in schema
    assert "tool_arguments" not in schema
    assert "credentials" not in schema


def test_terraform_separates_runtime_and_ci_identity_permissions() -> None:
    identities = read("infra/terraform/identities.tf")

    assert 'resource "azurerm_user_assigned_identity" "runtime"' in identities
    assert 'resource "azurerm_user_assigned_identity" "ci"' in identities
    assert 'role_definition_name = "AcrPull"' in identities
    assert "role_definition_id = local.key_vault_secrets_user_role_definition_id" in identities
    assert 'role_definition_name = "AcrPush"' in identities
    assert 'role_definition_name = "Container Apps Contributor"' in identities
    runtime_blocks = re.findall(
        r'resource "azurerm_role_assignment" "runtime_[^"]+" \{(.*?)\n\}',
        identities,
        flags=re.DOTALL,
    )
    assert runtime_blocks
    assert all("AcrPush" not in block for block in runtime_blocks)
    assert all("Container Apps Contributor" not in block for block in runtime_blocks)


def test_github_oidc_contract_is_disabled_and_parameterized_without_secret() -> None:
    identities = read("infra/terraform/identities.tf")
    variables = read("infra/terraform/variables.tf")
    checks = read("infra/terraform/checks.tf")
    all_tf = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(TF_ROOT.glob("*.tf"))
    ).lower()

    assert 'resource "azurerm_federated_identity_credential" "github"' in identities
    assert "var.github_federation_enabled ? 1 : 0" in identities
    assert "user_assigned_identity_id = azurerm_user_assigned_identity.ci.id" in identities
    assert "https://token.actions.githubusercontent.com" in identities
    assert "api://AzureADTokenExchange" in identities
    assert 'default     = false' in re.search(
        r'variable "github_federation_enabled" \{(.*?)\n\}',
        variables,
        flags=re.DOTALL,
    ).group(1)
    assert "placeholder-owner" in checks
    assert "azure_client_secret" not in all_tf


def test_container_app_enables_cloud_auth_durability_tls_and_disables_metrics() -> None:
    app = read("infra/terraform/container_apps.tf")

    for name, value in (
        ("OPENWEIGHT_CHECKPOINT_BACKEND", "postgres"),
        ("OPENWEIGHT_METRICS_ACCESS_MODE", "disabled"),
        ("POSTGRES_SSLMODE", "verify-full"),
        ("POSTGRES_SSLROOTCERT", "system"),
        ("LANGGRAPH_STRICT_MSGPACK", "true"),
    ):
        assert re.search(rf'name\s+=\s+"{name}"\s+value\s+=\s+"{value}"', app)
    assert 'name  = "OPENWEIGHT_AUTH_ENABLED"' in app
    assert "var.product_auth_enabled" in app
    assert 'resource "azurerm_key_vault_secret"' not in app


def test_requirements_pin_standard_durability_and_jwt_packages() -> None:
    requirements = read("requirements.txt")

    assert "langgraph-checkpoint-postgres==3.1.2" in requirements
    assert "psycopg-pool==3.3.1" in requirements
    assert "PyJWT[crypto]==2.13.0" in requirements
    assert "huggingface_hub==1.27.0" in requirements
