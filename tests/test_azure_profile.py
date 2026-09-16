from pathlib import Path

import pytest

from openweight_platform.api.config import ServiceSettings


@pytest.fixture
def ca_bundle(tmp_path: Path) -> Path:
    path = tmp_path / "ca-certificates.crt"
    path.write_text("test root bundle\n", encoding="utf-8")
    return path


def azure_settings(ca_bundle: Path, **overrides: str) -> ServiceSettings:
    return ServiceSettings.from_env(
        {
            "OPENWEIGHT_DEPLOYMENT_PROFILE": "azure",
            "OPENWEIGHT_ENVIRONMENT": "demo",
            "OPENWEIGHT_BACKEND": "gpt-oss",
            "OPENWEIGHT_GPT_OSS_MODEL_ID": "openai/gpt-oss-20b",
            "OPENWEIGHT_HF_PROVIDER": "groq",
            "OPENWEIGHT_DATABASE_REQUIRED": "true",
            "OPENWEIGHT_CHECKPOINT_BACKEND": "postgres",
            "OPENWEIGHT_WEB_ENABLED": "false",
            "OPENWEIGHT_METRICS_ENABLED": "false",
            "OPENWEIGHT_AUTH_ENABLED": "true",
            "OPENWEIGHT_AUTH_ISSUER": "https://login.example.invalid/tenant/v2.0",
            "OPENWEIGHT_AUTH_AUDIENCE": "api://openweight-platform",
            "OPENWEIGHT_AUTH_JWKS_URL": "https://login.example.invalid/keys",
            "OPENWEIGHT_PUBLIC_READ_ENABLED": "true",
            "POSTGRES_DB": "openweight_platform",
            "POSTGRES_USER": "openweight_app",
            "POSTGRES_PASSWORD": "test-only",  # pragma: allowlist secret
            "POSTGRES_HOST": "postgres.example.invalid",
            "POSTGRES_SSLMODE": "verify-full",
            "POSTGRES_SSLROOTCERT": str(ca_bundle),
            "HF_TOKEN": "test-only",  # pragma: allowlist secret
            **overrides,
        }
    )


def test_azure_profile_reuses_huggingface_groq_backend_with_durable_state(
    ca_bundle: Path,
) -> None:
    settings = azure_settings(ca_bundle)

    assert settings.configuration_errors == ()
    assert settings.deployment_profile == "azure"
    assert settings.uses_huggingface_inference is True
    assert settings.backend_config.inference_mode == "huggingface"
    assert settings.backend_config.huggingface_provider == "groq"
    assert settings.gpt_oss_model_id == "openai/gpt-oss-20b"
    assert settings.checkpoint_backend == "postgres"
    assert settings.database_config is not None
    assert settings.public_read_enabled is True
    assert "test-only" not in repr(settings)


def test_azure_profile_fails_closed_without_durability_auth_or_fixed_model(
    ca_bundle: Path,
) -> None:
    no_database = azure_settings(
        ca_bundle,
        OPENWEIGHT_DATABASE_REQUIRED="false",
    )
    memory = azure_settings(ca_bundle, OPENWEIGHT_CHECKPOINT_BACKEND="memory")
    no_auth = azure_settings(ca_bundle, OPENWEIGHT_AUTH_ENABLED="false")
    wrong_model = azure_settings(
        ca_bundle,
        OPENWEIGHT_GPT_OSS_MODEL_ID="other/model",
    )

    assert "requires PostgreSQL" in "; ".join(no_database.configuration_errors)
    assert "requires PostgreSQL checkpointing" in "; ".join(
        memory.configuration_errors
    )
    assert "requires authentication" in "; ".join(no_auth.configuration_errors)
    assert "requires openai/gpt-oss-20b" in "; ".join(
        wrong_model.configuration_errors
    )


def test_azure_profile_fails_closed_for_invalid_ca_bundle(
    ca_bundle: Path,
) -> None:
    missing = azure_settings(
        ca_bundle,
        POSTGRES_SSLROOTCERT=str(ca_bundle.parent / "missing.pem"),
    )
    implicit = azure_settings(ca_bundle, POSTGRES_SSLROOTCERT="system")

    for settings in (missing, implicit):
        assert settings.database_config is None
        assert "database configuration is incomplete or invalid" in "; ".join(
            settings.configuration_errors
        )
        assert "test-only" not in repr(settings)
