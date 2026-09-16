"""Environment-driven configuration for the OpenWeight HTTP service."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import quote, urlsplit

from openweight_platform.backends.factory import (
    DEFAULT_BACKEND_NAME,
    DEFAULT_GPT_OSS_MODEL_ID,
    ModelBackendConfig,
    SUPPORTED_BACKENDS,
)
from openweight_platform.backends.muse_glimmer import (
    DEFAULT_MUSE_BASE_URL,
    DEFAULT_MUSE_MODEL_ALIAS,
    DEFAULT_MUSE_TIMEOUT_SECONDS,
    SUPPORTED_REASONING_STRENGTHS,
)
from openweight_platform.postgres_tls import (
    POSTGRES_SSL_MODES,
    resolve_postgres_tls_config,
)
SERVICE_NAME = "openweight-platform"
DEFAULT_SERVICE_VERSION = "0.1.0"
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8000
DEFAULT_DATABASE_PROBE_TIMEOUT_SECONDS = 2
DEFAULT_OBSERVABILITY_SERVICE_NAME = SERVICE_NAME
OBSERVABILITY_SERVICE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MCP_SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MCP_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*$")
CHECKPOINT_BACKENDS = ("memory", "postgres")
METRICS_ACCESS_MODES = ("public", "protected", "disabled")
DEPLOYMENT_PROFILES = ("local", "huggingface", "azure")
HF_PROVIDER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
DEFAULT_HF_PROVIDER = "groq"
DEFAULT_HF_TIMEOUT_SECONDS = 60.0
DEFAULT_HF_INFERENCE_CONCURRENCY_LIMIT = 2
DEFAULT_HF_RATE_LIMIT_REQUESTS = 5
DEFAULT_HF_RATE_LIMIT_WINDOW_SECONDS = 60
DEFAULT_HF_RATE_LIMIT_MAX_CLIENTS = 1024
DEFAULT_DEMO_POLICY_INDEX = "deploy/huggingface/policy_index.npz"
DEFAULT_DEMO_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"


@dataclass(frozen=True)
class DatabaseConfig:
    """Small runtime-neutral PostgreSQL configuration value."""

    database: str
    user: str
    password: str = field(repr=False)
    host: str
    port: int
    sslmode: str = "prefer"
    sslrootcert: str | None = None

    @property
    def connect_kwargs(self) -> dict[str, str | int]:
        values: dict[str, str | int] = {
            "dbname": self.database,
            "user": self.user,
            "password": self.password,
            "host": self.host,
            "port": self.port,
            "sslmode": self.sslmode,
        }
        if self.sslrootcert is not None:
            values["sslrootcert"] = self.sslrootcert
        return values

    @property
    def sqlalchemy_url(self) -> str:
        url = (
            "postgresql+psycopg://"
            f"{quote(self.user, safe='')}:{quote(self.password, safe='')}@"
            f"{quote(self.host, safe='')}:{self.port}/"
            f"{quote(self.database, safe='')}"
        )
        parameters = [f"sslmode={quote(self.sslmode, safe='')}"]
        if self.sslrootcert is not None:
            parameters.append(f"sslrootcert={quote(self.sslrootcert, safe='')}")
        return f"{url}?{'&'.join(parameters)}"

    @property
    def conninfo(self) -> str:
        return self.sqlalchemy_url.replace(
            "postgresql+psycopg://",
            "postgresql://",
            1,
        )


def _integer(
    values: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    raw = values.get(name, str(default))
    try:
        parsed = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if parsed < minimum or (maximum is not None and parsed > maximum):
        limit = f" between {minimum} and {maximum}" if maximum else f" >= {minimum}"
        raise ValueError(f"{name} must be{limit}")
    return parsed


def _float(
    values: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float,
) -> float:
    raw = values.get(name, str(default))
    try:
        parsed = float(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be numeric") from error
    if parsed < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return parsed


def _boolean(
    values: Mapping[str, str],
    name: str,
    default: bool,
) -> bool:
    raw = values.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _optional_text(values: Mapping[str, str], name: str) -> str | None:
    value = values.get(name)
    return value.strip() if value and value.strip() else None


@dataclass(frozen=True)
class ServiceSettings:
    """Validated service configuration without public secret serialization."""

    environment: str
    service_version: str
    build_sha: str | None
    log_level: str
    api_host: str
    api_port: int
    deployment_profile: Literal["local", "huggingface", "azure"]
    backend_name: str
    max_new_tokens: int
    gpt_oss_model_id: str
    muse_base_url: str
    muse_model_alias: str
    muse_timeout_seconds: float
    reasoning_strength: str
    hf_token: str | None = field(repr=False)
    hf_provider: str
    hf_timeout_seconds: float
    hf_max_retries: int
    hf_inference_concurrency_limit: int
    hf_rate_limit_requests: int
    hf_rate_limit_window_seconds: int
    hf_rate_limit_max_clients: int
    hf_public_origin: str | None
    demo_policy_index: str
    demo_embedding_model: str
    web_enabled: bool
    tavily_configured: bool
    database_required: bool
    database_config: DatabaseConfig | None
    database_probe_timeout_seconds: int
    checkpoint_backend: Literal["memory", "postgres"]
    mlflow_configured: bool
    observability_enabled: bool
    metrics_enabled: bool
    metrics_access_mode: Literal["public", "protected", "disabled"]
    tracing_enabled: bool
    observability_service_name: str
    otlp_endpoint: str | None = field(repr=False)
    auth_enabled: bool = False
    public_read_enabled: bool = False
    auth_issuer: str | None = None
    auth_audience: str | None = None
    auth_jwks_url: str | None = field(default=None, repr=False)
    mcp_enabled: bool = False
    mcp_path: str = "/mcp"
    mcp_server_name: str = SERVICE_NAME
    mcp_resource_server_url: str | None = None
    mcp_allowed_hosts: tuple[str, ...] = (
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
    )
    mcp_allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
    )
    frontend_enabled: bool = False
    frontend_dist_dir: str = "frontend/dist"
    configuration_errors: tuple[str, ...] = ()

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "ServiceSettings":
        values = os.environ if environ is None else environ
        environment = values.get("OPENWEIGHT_ENVIRONMENT", "development").strip()
        service_version = values.get(
            "OPENWEIGHT_SERVICE_VERSION",
            DEFAULT_SERVICE_VERSION,
        ).strip()
        log_level = values.get("OPENWEIGHT_LOG_LEVEL", "INFO").strip().upper()
        deployment_profile = values.get(
            "OPENWEIGHT_DEPLOYMENT_PROFILE",
            "local",
        ).strip().lower()
        backend_name = values.get(
            "OPENWEIGHT_BACKEND",
            DEFAULT_BACKEND_NAME,
        ).strip()
        gpt_oss_model_id = values.get(
            "OPENWEIGHT_GPT_OSS_MODEL_ID",
            DEFAULT_GPT_OSS_MODEL_ID,
        ).strip()
        reasoning_strength = values.get(
            "OPENWEIGHT_REASONING_STRENGTH",
            "low",
        ).strip()

        if not environment:
            raise ValueError("OPENWEIGHT_ENVIRONMENT must not be blank")
        if not service_version:
            raise ValueError("OPENWEIGHT_SERVICE_VERSION must not be blank")
        if deployment_profile not in DEPLOYMENT_PROFILES:
            raise ValueError(
                "OPENWEIGHT_DEPLOYMENT_PROFILE must be one of: "
                + ", ".join(DEPLOYMENT_PROFILES)
            )
        if (
            log_level not in logging.getLevelNamesMapping()
            or log_level == "NOTSET"
        ):
            raise ValueError("OPENWEIGHT_LOG_LEVEL must be a standard log level")
        if backend_name not in SUPPORTED_BACKENDS:
            raise ValueError(
                "OPENWEIGHT_BACKEND must be one of: "
                + ", ".join(SUPPORTED_BACKENDS)
            )
        if not gpt_oss_model_id:
            raise ValueError("OPENWEIGHT_GPT_OSS_MODEL_ID must not be blank")
        if reasoning_strength not in SUPPORTED_REASONING_STRENGTHS:
            raise ValueError(
                "OPENWEIGHT_REASONING_STRENGTH must be one of: "
                + ", ".join(SUPPORTED_REASONING_STRENGTHS)
            )

        database_required = _boolean(
            values,
            "OPENWEIGHT_DATABASE_REQUIRED",
            deployment_profile != "huggingface",
        )
        checkpoint_backend = values.get(
            "OPENWEIGHT_CHECKPOINT_BACKEND",
            "memory",
        ).strip().lower()
        if checkpoint_backend not in CHECKPOINT_BACKENDS:
            raise ValueError(
                "OPENWEIGHT_CHECKPOINT_BACKEND must be one of: "
                + ", ".join(CHECKPOINT_BACKENDS)
            )
        web_enabled = _boolean(values, "OPENWEIGHT_WEB_ENABLED", False)
        observability_enabled = _boolean(
            values,
            "OPENWEIGHT_OBSERVABILITY_ENABLED",
            True,
        )
        metrics_enabled = observability_enabled and _boolean(
            values,
            "OPENWEIGHT_METRICS_ENABLED",
            True,
        )
        metrics_access_mode = values.get(
            "OPENWEIGHT_METRICS_ACCESS_MODE",
            "public",
        ).strip().lower()
        if metrics_access_mode not in METRICS_ACCESS_MODES:
            raise ValueError(
                "OPENWEIGHT_METRICS_ACCESS_MODE must be one of: "
                + ", ".join(METRICS_ACCESS_MODES)
            )
        if not metrics_enabled:
            metrics_access_mode = "disabled"
        elif metrics_access_mode == "disabled":
            metrics_enabled = False
        auth_enabled = _boolean(values, "OPENWEIGHT_AUTH_ENABLED", False)
        public_read_enabled = _boolean(
            values,
            "OPENWEIGHT_PUBLIC_READ_ENABLED",
            False,
        )
        auth_issuer = _optional_text(values, "OPENWEIGHT_AUTH_ISSUER")
        auth_audience = _optional_text(values, "OPENWEIGHT_AUTH_AUDIENCE")
        auth_jwks_url = _optional_text(values, "OPENWEIGHT_AUTH_JWKS_URL")
        mcp_enabled = _boolean(values, "OPENWEIGHT_MCP_ENABLED", False)
        mcp_path = values.get("OPENWEIGHT_MCP_PATH", "/mcp").strip()
        mcp_server_name = values.get(
            "OPENWEIGHT_MCP_SERVER_NAME",
            SERVICE_NAME,
        ).strip()
        mcp_resource_server_url = _optional_text(
            values,
            "OPENWEIGHT_MCP_RESOURCE_SERVER_URL",
        )
        mcp_allowed_hosts = tuple(
            item.strip()
            for item in values.get(
                "OPENWEIGHT_MCP_ALLOWED_HOSTS",
                "127.0.0.1:*,localhost:*,[::1]:*",
            ).split(",")
            if item.strip()
        )
        mcp_allowed_origins = tuple(
            item.strip()
            for item in values.get(
                "OPENWEIGHT_MCP_ALLOWED_ORIGINS",
                "http://127.0.0.1:*,http://localhost:*,http://[::1]:*",
            ).split(",")
            if item.strip()
        )
        frontend_enabled = _boolean(
            values,
            "OPENWEIGHT_FRONTEND_ENABLED",
            False,
        )
        frontend_dist_dir = values.get(
            "OPENWEIGHT_FRONTEND_DIST_DIR",
            "frontend/dist",
        ).strip()
        if frontend_enabled and not frontend_dist_dir:
            raise ValueError("OPENWEIGHT_FRONTEND_DIST_DIR must not be blank")
        tracing_enabled = observability_enabled and _boolean(
            values,
            "OPENWEIGHT_TRACING_ENABLED",
            False,
        )
        observability_service_name = values.get(
            "OPENWEIGHT_OBSERVABILITY_SERVICE_NAME",
            DEFAULT_OBSERVABILITY_SERVICE_NAME,
        ).strip()
        if not OBSERVABILITY_SERVICE_NAME_PATTERN.fullmatch(
            observability_service_name
        ):
            raise ValueError(
                "OPENWEIGHT_OBSERVABILITY_SERVICE_NAME must contain only "
                "letters, digits, dots, underscores, or hyphens"
            )
        otlp_endpoint = _optional_text(values, "OPENWEIGHT_OTLP_ENDPOINT")
        if otlp_endpoint is not None:
            parsed_endpoint = urlsplit(otlp_endpoint)
            if (
                parsed_endpoint.scheme not in {"http", "https"}
                or not parsed_endpoint.hostname
                or parsed_endpoint.username is not None
                or parsed_endpoint.password is not None
            ):
                raise ValueError(
                    "OPENWEIGHT_OTLP_ENDPOINT must be an HTTP(S) URL without credentials"
                )
        tavily_configured = bool(_optional_text(values, "TAVILY_API_KEY"))
        hf_token = _optional_text(values, "HF_TOKEN")
        hf_provider = values.get(
            "OPENWEIGHT_HF_PROVIDER",
            DEFAULT_HF_PROVIDER,
        ).strip().lower()
        if hf_provider == "auto" or not HF_PROVIDER_PATTERN.fullmatch(hf_provider):
            raise ValueError(
                "OPENWEIGHT_HF_PROVIDER must name one explicit provider"
            )
        hf_timeout_seconds = _float(
            values,
            "OPENWEIGHT_HF_TIMEOUT_SECONDS",
            DEFAULT_HF_TIMEOUT_SECONDS,
            minimum=1.0,
        )
        if hf_timeout_seconds > 120:
            raise ValueError("OPENWEIGHT_HF_TIMEOUT_SECONDS must be <= 120")
        hf_max_retries = _integer(
            values,
            "OPENWEIGHT_HF_MAX_RETRIES",
            0,
            minimum=0,
            maximum=0,
        )
        hf_inference_concurrency_limit = _integer(
            values,
            "OPENWEIGHT_HF_INFERENCE_CONCURRENCY_LIMIT",
            DEFAULT_HF_INFERENCE_CONCURRENCY_LIMIT,
            minimum=1,
            maximum=8,
        )
        hf_rate_limit_requests = _integer(
            values,
            "OPENWEIGHT_HF_RATE_LIMIT_REQUESTS",
            DEFAULT_HF_RATE_LIMIT_REQUESTS,
            minimum=1,
            maximum=60,
        )
        hf_rate_limit_window_seconds = _integer(
            values,
            "OPENWEIGHT_HF_RATE_LIMIT_WINDOW_SECONDS",
            DEFAULT_HF_RATE_LIMIT_WINDOW_SECONDS,
            minimum=10,
            maximum=3600,
        )
        hf_rate_limit_max_clients = _integer(
            values,
            "OPENWEIGHT_HF_RATE_LIMIT_MAX_CLIENTS",
            DEFAULT_HF_RATE_LIMIT_MAX_CLIENTS,
            minimum=2,
            maximum=10000,
        )
        hf_public_origin = _optional_text(values, "OPENWEIGHT_HF_PUBLIC_ORIGIN")
        if hf_public_origin is not None:
            parsed_origin = urlsplit(hf_public_origin)
            if (
                parsed_origin.scheme != "https"
                or not parsed_origin.hostname
                or parsed_origin.username is not None
                or parsed_origin.password is not None
                or parsed_origin.port is not None
                or parsed_origin.path not in ("", "/")
                or parsed_origin.query
                or parsed_origin.fragment
                or "*" in parsed_origin.hostname
                or parsed_origin.hostname in {"localhost", "127.0.0.1", "::1"}
            ):
                raise ValueError(
                    "OPENWEIGHT_HF_PUBLIC_ORIGIN must be one exact public HTTPS origin"
                )
            hf_public_origin = f"https://{parsed_origin.hostname.lower()}"
            mcp_allowed_hosts = (parsed_origin.hostname.lower(),)
            mcp_allowed_origins = (hf_public_origin,)
        demo_policy_index = values.get(
            "OPENWEIGHT_DEMO_POLICY_INDEX",
            DEFAULT_DEMO_POLICY_INDEX,
        ).strip()
        if not demo_policy_index:
            raise ValueError("OPENWEIGHT_DEMO_POLICY_INDEX must not be blank")
        demo_embedding_model = values.get(
            "OPENWEIGHT_DEMO_EMBEDDING_MODEL",
            DEFAULT_DEMO_EMBEDDING_MODEL,
        ).strip()
        if not demo_embedding_model:
            raise ValueError(
                "OPENWEIGHT_DEMO_EMBEDDING_MODEL must not be blank"
            )
        errors: list[str] = []

        database_config: DatabaseConfig | None = None
        database_values_present = deployment_profile != "huggingface" and any(
            values.get(name)
            for name in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
        )
        if database_values_present:
            try:
                database_port = _integer(
                    values,
                    "POSTGRES_PORT",
                    5432,
                    minimum=1,
                    maximum=65535,
                )
                database = values.get("POSTGRES_DB", "").strip()
                user = values.get("POSTGRES_USER", "").strip()
                password = values.get("POSTGRES_PASSWORD", "")
                host = values.get("POSTGRES_HOST", "localhost").strip()
                sslmode, sslrootcert = resolve_postgres_tls_config(
                    values.get("POSTGRES_SSLMODE", "prefer"),
                    values.get("POSTGRES_SSLROOTCERT"),
                )
                if not all((database, user, password, host)):
                    raise ValueError("database configuration is incomplete")
                database_config = DatabaseConfig(
                    database=database,
                    user=user,
                    password=password,
                    host=host,
                    port=database_port,
                    sslmode=sslmode,
                    sslrootcert=sslrootcert,
                )
            except ValueError:
                errors.append("database configuration is incomplete or invalid")
        elif database_required:
            errors.append("required database configuration is missing")

        if deployment_profile == "huggingface":
            if database_required:
                errors.append(
                    "Hugging Face demo profile cannot require PostgreSQL"
                )
            if checkpoint_backend != "memory":
                errors.append(
                    "Hugging Face demo profile requires memory checkpointing"
                )
            if backend_name != "gpt-oss":
                errors.append("Hugging Face demo profile requires gpt-oss")
            if gpt_oss_model_id != DEFAULT_GPT_OSS_MODEL_ID:
                errors.append(
                    "Hugging Face demo profile requires openai/gpt-oss-20b"
                )
            if web_enabled:
                errors.append(
                    "Hugging Face demo profile disables external web search"
                )
        if deployment_profile == "azure":
            if not database_required:
                errors.append("Azure profile requires PostgreSQL")
            if checkpoint_backend != "postgres":
                errors.append("Azure profile requires PostgreSQL checkpointing")
            if backend_name != "gpt-oss":
                errors.append("Azure profile requires gpt-oss")
            if gpt_oss_model_id != DEFAULT_GPT_OSS_MODEL_ID:
                errors.append("Azure profile requires openai/gpt-oss-20b")
            if web_enabled:
                errors.append("Azure profile disables external web search")
            if not auth_enabled:
                errors.append(
                    "Azure profile requires authentication for governed actions"
                )

        if web_enabled and not tavily_configured:
            errors.append("web search is enabled but TAVILY_API_KEY is missing")
        if tracing_enabled and otlp_endpoint is None:
            errors.append(
                "tracing is enabled but OPENWEIGHT_OTLP_ENDPOINT is missing"
            )
        if checkpoint_backend == "postgres" and database_config is None:
            errors.append(
                "PostgreSQL checkpointing requires database configuration"
            )
        if auth_enabled:
            if not all((auth_issuer, auth_audience, auth_jwks_url)):
                errors.append("authentication configuration is incomplete")
            else:
                for name, value in (
                    ("issuer", auth_issuer),
                    ("JWKS URL", auth_jwks_url),
                ):
                    parsed = urlsplit(value)
                    if (
                        parsed.scheme != "https"
                        or not parsed.hostname
                        or parsed.username is not None
                        or parsed.password is not None
                    ):
                        errors.append(
                            f"authentication {name} must be an HTTPS URL without credentials"
                        )
        if mcp_enabled:
            if not MCP_PATH_PATTERN.fullmatch(mcp_path):
                errors.append("MCP path must be a safe absolute URL path")
            if not MCP_SERVER_NAME_PATTERN.fullmatch(mcp_server_name):
                errors.append("MCP server name is invalid")
            if not mcp_allowed_hosts:
                errors.append("MCP allowed-host list must not be empty")
            if any(
                any(character.isspace() for character in item)
                or "/" in item
                or "@" in item
                for item in mcp_allowed_hosts
            ):
                errors.append("MCP allowed-host list is invalid")
            if any(
                not item.startswith(("http://", "https://"))
                or "@" in item
                for item in mcp_allowed_origins
            ):
                errors.append("MCP allowed-origin list is invalid")
            if auth_enabled and mcp_resource_server_url is None:
                errors.append(
                    "authenticated MCP requires OPENWEIGHT_MCP_RESOURCE_SERVER_URL"
                )
            if mcp_resource_server_url is not None:
                parsed_mcp_url = urlsplit(mcp_resource_server_url)
                allowed_schemes = (
                    {"https"}
                    if environment in {"production", "demo"}
                    else {"http", "https"}
                )
                if (
                    parsed_mcp_url.scheme not in allowed_schemes
                    or not parsed_mcp_url.hostname
                    or parsed_mcp_url.username is not None
                    or parsed_mcp_url.password is not None
                    or parsed_mcp_url.query
                    or parsed_mcp_url.fragment
                ):
                    errors.append(
                        "MCP resource-server URL must be a credential-free HTTP(S) URL"
                    )
        if environment in {"production", "demo"}:
            if database_config is not None and database_config.sslmode != "verify-full":
                errors.append(
                    "production/demo PostgreSQL requires sslmode=verify-full"
                )
            if metrics_access_mode == "public":
                errors.append(
                    "production/demo metrics cannot use public access mode"
                )

        return cls(
            environment=environment,
            service_version=service_version,
            build_sha=_optional_text(values, "OPENWEIGHT_BUILD_SHA"),
            log_level=log_level,
            api_host=values.get("OPENWEIGHT_API_HOST", DEFAULT_API_HOST).strip(),
            api_port=_integer(
                values,
                "OPENWEIGHT_API_PORT",
                DEFAULT_API_PORT,
                minimum=1,
                maximum=65535,
            ),
            deployment_profile=deployment_profile,  # type: ignore[arg-type]
            backend_name=backend_name,
            max_new_tokens=_integer(
                values,
                "OPENWEIGHT_MAX_NEW_TOKENS",
                768,
                minimum=1,
            ),
            gpt_oss_model_id=gpt_oss_model_id,
            muse_base_url=values.get(
                "OPENWEIGHT_MUSE_BASE_URL",
                DEFAULT_MUSE_BASE_URL,
            ).strip(),
            muse_model_alias=values.get(
                "OPENWEIGHT_MUSE_MODEL_ALIAS",
                DEFAULT_MUSE_MODEL_ALIAS,
            ).strip(),
            muse_timeout_seconds=_float(
                values,
                "OPENWEIGHT_MUSE_TIMEOUT_SECONDS",
                DEFAULT_MUSE_TIMEOUT_SECONDS,
                minimum=0.1,
            ),
            reasoning_strength=reasoning_strength,
            hf_token=hf_token,
            hf_provider=hf_provider,
            hf_timeout_seconds=hf_timeout_seconds,
            hf_max_retries=hf_max_retries,
            hf_inference_concurrency_limit=hf_inference_concurrency_limit,
            hf_rate_limit_requests=hf_rate_limit_requests,
            hf_rate_limit_window_seconds=hf_rate_limit_window_seconds,
            hf_rate_limit_max_clients=hf_rate_limit_max_clients,
            hf_public_origin=hf_public_origin,
            demo_policy_index=demo_policy_index,
            demo_embedding_model=demo_embedding_model,
            web_enabled=web_enabled,
            tavily_configured=tavily_configured,
            database_required=database_required,
            database_config=database_config,
            database_probe_timeout_seconds=_integer(
                values,
                "OPENWEIGHT_DATABASE_PROBE_TIMEOUT_SECONDS",
                DEFAULT_DATABASE_PROBE_TIMEOUT_SECONDS,
                minimum=1,
                maximum=30,
            ),
            checkpoint_backend=checkpoint_backend,  # type: ignore[arg-type]
            mlflow_configured=bool(
                _optional_text(values, "MLFLOW_TRACKING_URI")
            ),
            observability_enabled=observability_enabled,
            metrics_enabled=metrics_enabled,
            metrics_access_mode=metrics_access_mode,  # type: ignore[arg-type]
            tracing_enabled=tracing_enabled,
            observability_service_name=observability_service_name,
            otlp_endpoint=otlp_endpoint,
            auth_enabled=auth_enabled,
            public_read_enabled=public_read_enabled,
            auth_issuer=auth_issuer,
            auth_audience=auth_audience,
            auth_jwks_url=auth_jwks_url,
            mcp_enabled=mcp_enabled,
            mcp_path=mcp_path,
            mcp_server_name=mcp_server_name,
            mcp_resource_server_url=mcp_resource_server_url,
            mcp_allowed_hosts=mcp_allowed_hosts,
            mcp_allowed_origins=mcp_allowed_origins,
            frontend_enabled=frontend_enabled,
            frontend_dist_dir=frontend_dist_dir,
            configuration_errors=tuple(errors),
        )

    @property
    def backend_config(self) -> ModelBackendConfig:
        return ModelBackendConfig(
            backend_name=self.backend_name,  # type: ignore[arg-type]
            max_new_tokens=self.max_new_tokens,
            gpt_oss_model_id=self.gpt_oss_model_id,
            muse_base_url=self.muse_base_url,
            muse_model_alias=self.muse_model_alias,
            muse_timeout_seconds=self.muse_timeout_seconds,
            reasoning_strength=self.reasoning_strength,  # type: ignore[arg-type]
            inference_mode=(
                "huggingface"
                if self.uses_huggingface_inference
                else "local"
            ),
            huggingface_token=self.hf_token,
            huggingface_provider=self.hf_provider,
            huggingface_timeout_seconds=self.hf_timeout_seconds,
            huggingface_max_retries=self.hf_max_retries,
        )

    @property
    def uses_huggingface_inference(self) -> bool:
        """Return whether generation uses the shared routed HF client."""

        return self.deployment_profile in {"huggingface", "azure"}


__all__ = [
    "CHECKPOINT_BACKENDS",
    "DEFAULT_DEMO_POLICY_INDEX",
    "DEFAULT_DEMO_EMBEDDING_MODEL",
    "DEFAULT_HF_PROVIDER",
    "DEFAULT_HF_INFERENCE_CONCURRENCY_LIMIT",
    "DEFAULT_HF_RATE_LIMIT_MAX_CLIENTS",
    "DEFAULT_HF_RATE_LIMIT_REQUESTS",
    "DEFAULT_HF_RATE_LIMIT_WINDOW_SECONDS",
    "DEFAULT_HF_TIMEOUT_SECONDS",
    "DEPLOYMENT_PROFILES",
    "DatabaseConfig",
    "DEFAULT_API_HOST",
    "DEFAULT_API_PORT",
    "DEFAULT_OBSERVABILITY_SERVICE_NAME",
    "DEFAULT_SERVICE_VERSION",
    "METRICS_ACCESS_MODES",
    "MCP_PATH_PATTERN",
    "MCP_SERVER_NAME_PATTERN",
    "OBSERVABILITY_SERVICE_NAME_PATTERN",
    "POSTGRES_SSL_MODES",
    "SERVICE_NAME",
    "ServiceSettings",
]
