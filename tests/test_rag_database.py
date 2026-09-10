import pytest

from openweight_platform.rag.database import PostgresConfig


def test_postgres_config_uses_required_values_and_defaults():
    config = PostgresConfig.from_env(
        {
            "POSTGRES_DB": "policy_db",
            "POSTGRES_USER": "policy_user",
            "POSTGRES_PASSWORD": "secret",
        }
    )

    assert config.database == "policy_db"
    assert config.user == "policy_user"
    assert config.password == "secret"
    assert config.host == "localhost"
    assert config.port == 5432
    assert config.connect_kwargs == {
        "dbname": "policy_db",
        "user": "policy_user",
        "password": "secret",
        "host": "localhost",
        "port": 5432,
        "sslmode": "prefer",
    }


def test_postgres_config_accepts_host_port_and_escapes_url_values():
    config = PostgresConfig.from_env(
        {
            "POSTGRES_DB": "policy db",
            "POSTGRES_USER": "policy@example",
            "POSTGRES_PASSWORD": "p@ss/word",
            "POSTGRES_HOST": "database.internal",
            "POSTGRES_PORT": "6432",
        }
    )

    assert config.host == "database.internal"
    assert config.port == 6432
    assert config.sqlalchemy_url == (
        "postgresql+psycopg://policy%40example:p%40ss%2Fword@"
        "database.internal:6432/policy db?sslmode=prefer"
    )


def test_postgres_config_supports_certificate_verification() -> None:
    config = PostgresConfig.from_env(
        {
            "POSTGRES_DB": "policy_db",
            "POSTGRES_USER": "policy_user",
            "POSTGRES_PASSWORD": "placeholder",  # pragma: allowlist secret
            "POSTGRES_SSLMODE": "verify-full",
            "POSTGRES_SSLROOTCERT": "system",
        }
    )

    assert config.connect_kwargs["sslmode"] == "verify-full"
    assert config.connect_kwargs["sslrootcert"] == "system"
    assert "sslmode=verify-full" in config.sqlalchemy_url
    assert "sslrootcert=system" in config.sqlalchemy_url


def test_postgres_config_rejects_missing_credentials():
    with pytest.raises(RuntimeError, match="POSTGRES_PASSWORD"):
        PostgresConfig.from_env(
            {
                "POSTGRES_DB": "policy_db",
                "POSTGRES_USER": "policy_user",
            }
        )
