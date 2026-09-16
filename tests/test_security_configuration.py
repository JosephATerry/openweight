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


def terraform_variable_block(source: str, name: str) -> str:
    match = re.search(
        rf'variable "{re.escape(name)}" \{{(?P<body>.*?)\n\}}',
        source,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group("body")


def terraform_string_default(source: str, name: str) -> str:
    block = terraform_variable_block(source, name)
    match = re.search(r'default\s*=\s*"(?P<value>[^"]+)"', block)
    assert match is not None
    return match.group("value")


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
    locals_tf = read("infra/terraform/locals.tf")
    checks = read("infra/terraform/checks.tf")
    example = read("infra/terraform/terraform.tfvars.example")
    oidc_docs = "\n".join(
        read(path)
        for path in (
            "infra/terraform/README.md",
            "docs/azure_architecture.md",
            "docs/ci_cd.md",
            "docs/security.md",
        )
    )
    all_tf = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(TF_ROOT.glob("*.tf"))
    ).lower()

    assert 'resource "azurerm_federated_identity_credential" "github"' in identities
    assert "var.github_federation_enabled ? 1 : 0" in identities
    assert "user_assigned_identity_id = azurerm_user_assigned_identity.ci.id" in identities
    assert re.search(
        r'issuer\s*=\s*"https://token\.actions\.githubusercontent\.com"',
        identities,
    )
    assert re.search(
        r'audience\s*=\s*\["api://AzureADTokenExchange"\]',
        identities,
    )
    assert 'default     = false' in re.search(
        r'variable "github_federation_enabled" \{(.*?)\n\}',
        variables,
        flags=re.DOTALL,
    ).group(1)
    assert "placeholder-owner" in checks
    assert "github_repository_owner_id" in checks
    assert "github_repository_id" in checks
    for variable in ("github_repository_owner_id", "github_repository_id"):
        block = terraform_variable_block(variables, variable)
        assert "type        = string" in block
        pattern_match = re.search(r'can\(regex\("(?P<pattern>[^\"]+)"', block)
        assert pattern_match is not None
        pattern = pattern_match.group("pattern")
        assert re.fullmatch(pattern, terraform_string_default(variables, variable))
        assert all(
            re.fullmatch(pattern, invalid) is None
            for invalid in ("", "0", "01", "-1", "123x", "1.5")
        )

    owner = terraform_string_default(variables, "github_repository_owner")
    owner_id = terraform_string_default(variables, "github_repository_owner_id")
    repository = terraform_string_default(variables, "github_repository")
    repository_id = terraform_string_default(variables, "github_repository_id")
    environment = terraform_string_default(variables, "github_environment")
    subject = (
        f"repo:{owner}@{owner_id}/{repository}@{repository_id}:"
        f"environment:{environment}"
    )
    assert subject == (
        "repo:JosephATerry@204825811/openweight@1363713223:"
        "environment:azure-production"
    )
    assert "${var.github_repository_owner}@${var.github_repository_owner_id}" in locals_tf
    assert "/${var.github_repository}@${var.github_repository_id}" in locals_tf
    assert ":environment:${var.github_environment}" in locals_tf
    assert "repo:${var.github_repository_owner}/${var.github_repository}" not in locals_tf
    assert "*" not in "\n".join(
        line for line in locals_tf.splitlines() if "github_oidc" in line
    )
    assert "repo:JosephATerry/openweight:environment:azure-production" not in oidc_docs
    assert "*" not in subject
    assert re.search(
        rf'^github_repository_owner_id\s*=\s*"{re.escape(owner_id)}"$',
        example,
        flags=re.MULTILINE,
    )
    assert re.search(
        rf'^github_repository_id\s*=\s*"{re.escape(repository_id)}"$',
        example,
        flags=re.MULTILINE,
    )
    assert "azure_client_secret" not in all_tf


def test_container_app_enables_cloud_auth_durability_tls_and_disables_metrics() -> None:
    app = read("infra/terraform/container_apps.tf")

    for name, value in (
        ("OPENWEIGHT_CHECKPOINT_BACKEND", "postgres"),
        ("OPENWEIGHT_METRICS_ACCESS_MODE", "disabled"),
        ("POSTGRES_SSLMODE", "verify-full"),
        ("LANGGRAPH_STRICT_MSGPACK", "true"),
    ):
        assert re.search(rf'name\s+=\s+"{name}"\s+value\s+=\s+"{value}"', app)
    assert 'name  = "OPENWEIGHT_AUTH_ENABLED"' in app
    assert "var.product_auth_enabled" in app
    assert 'resource "azurerm_key_vault_secret"' not in app


def test_azure_database_tls_uses_one_explicit_container_ca_bundle() -> None:
    dockerfile = read("Dockerfile")
    terraform = "\n".join(
        read(path)
        for path in (
            "infra/terraform/bootstrap_jobs.tf",
            "infra/terraform/container_apps.tf",
        )
    )
    migration = read("scripts/migrate_database.py")
    indexing = read("scripts/index_policy_corpus.py")
    rag_config = read("src/openweight_platform/rag/database.py")
    runtime_config = read("src/openweight_platform/api/config.py")

    assert (
        "POSTGRES_SSLROOTCERT=/etc/ssl/certs/ca-certificates.crt" in dockerfile
    )
    assert 'test -f "${POSTGRES_SSLROOTCERT}"' in dockerfile
    assert 'test -r "${POSTGRES_SSLROOTCERT}"' in dockerfile
    assert 'test -s "${POSTGRES_SSLROOTCERT}"' in dockerfile
    assert "POSTGRES_SSLROOTCERT" not in terraform
    assert 'name  = "POSTGRES_SSLMODE"' in terraform
    assert 'value = "verify-full"' in terraform
    assert "PostgresConfig.from_env()" in migration
    assert "PostgresConfig.from_env()" in indexing
    assert "resolve_postgres_tls_config" in rag_config
    assert "resolve_postgres_tls_config" in runtime_config
    assert "sslmode=disable" not in terraform
    assert "sslmode=require" not in terraform
    assert "sslmode=verify-ca" not in terraform


def test_requirements_pin_standard_durability_and_jwt_packages() -> None:
    requirements = read("requirements.txt")

    assert "langgraph-checkpoint-postgres==3.1.2" in requirements
    assert "psycopg-pool==3.3.1" in requirements
    assert "PyJWT[crypto]==2.13.0" in requirements
    assert "huggingface_hub==1.27.0" in requirements
