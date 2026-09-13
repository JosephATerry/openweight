from openweight_platform.api.config import ServiceSettings


def azure_settings(**overrides: str) -> ServiceSettings:
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
            "POSTGRES_SSLROOTCERT": "system",
            "HF_TOKEN": "test-only",  # pragma: allowlist secret
            **overrides,
        }
    )


def test_azure_profile_reuses_huggingface_groq_backend_with_durable_state() -> None:
    settings = azure_settings()

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


def test_azure_profile_fails_closed_without_durability_auth_or_fixed_model() -> None:
    no_database = azure_settings(OPENWEIGHT_DATABASE_REQUIRED="false")
    memory = azure_settings(OPENWEIGHT_CHECKPOINT_BACKEND="memory")
    no_auth = azure_settings(OPENWEIGHT_AUTH_ENABLED="false")
    wrong_model = azure_settings(OPENWEIGHT_GPT_OSS_MODEL_ID="other/model")

    assert "requires PostgreSQL" in "; ".join(no_database.configuration_errors)
    assert "requires PostgreSQL checkpointing" in "; ".join(
        memory.configuration_errors
    )
    assert "requires authentication" in "; ".join(no_auth.configuration_errors)
    assert "requires openai/gpt-oss-20b" in "; ".join(
        wrong_model.configuration_errors
    )
