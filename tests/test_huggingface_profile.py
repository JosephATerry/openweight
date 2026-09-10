from __future__ import annotations

from pathlib import Path

import pytest

from openweight_platform.api.config import ServiceSettings
from openweight_platform.api.errors import DependencyUnavailableError, InvalidRequestError
from openweight_platform.api.observability import Observability
from openweight_platform.api.runtime import DefaultPlatformRuntime
from openweight_platform.orchestration.approval import ApprovalDecision


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "deploy/huggingface/policy_index.npz"


def settings(**overrides: str) -> ServiceSettings:
    return ServiceSettings.from_env({
        "OPENWEIGHT_DEPLOYMENT_PROFILE": "huggingface",
        "OPENWEIGHT_ENVIRONMENT": "demo",
        "OPENWEIGHT_DATABASE_REQUIRED": "false",
        "OPENWEIGHT_CHECKPOINT_BACKEND": "memory",
        "OPENWEIGHT_METRICS_ENABLED": "false",
        "OPENWEIGHT_AUTH_ENABLED": "false",
        "OPENWEIGHT_DEMO_POLICY_INDEX": str(INDEX),
        **overrides,
    })


def observability() -> Observability:
    return Observability(
        service_name="openweight-test",
        environment="test",
        metrics_enabled=False,
        tracing_enabled=False,
    )


def test_huggingface_profile_is_explicit_and_local_defaults_are_unchanged() -> None:
    local = ServiceSettings.from_env({
        "OPENWEIGHT_ENVIRONMENT": "test",
        "OPENWEIGHT_DATABASE_REQUIRED": "false",
    })
    public = settings(HF_TOKEN="unit-test-token")  # pragma: allowlist secret

    assert local.deployment_profile == "local"
    assert local.backend_config.inference_mode == "local"
    assert local.database_required is False
    assert public.deployment_profile == "huggingface"
    assert public.backend_name == "gpt-oss"
    assert public.gpt_oss_model_id == "openai/gpt-oss-20b"
    assert public.backend_config.inference_mode == "huggingface"
    assert public.database_config is None
    assert public.web_enabled is False
    assert public.hf_max_retries == 0
    assert public.hf_inference_concurrency_limit == 2
    assert public.hf_rate_limit_requests == 5
    assert public.hf_rate_limit_window_seconds == 60
    assert public.hf_rate_limit_max_clients == 1024
    assert "unit-test-token" not in repr(public)


def test_public_profile_rejects_database_durability_and_automatic_provider() -> None:
    database = settings(OPENWEIGHT_DATABASE_REQUIRED="true")
    checkpoint = settings(OPENWEIGHT_CHECKPOINT_BACKEND="postgres")
    wrong_model = settings(OPENWEIGHT_GPT_OSS_MODEL_ID="other/model")
    web = settings(
        OPENWEIGHT_WEB_ENABLED="true",
        TAVILY_API_KEY="test-only",  # pragma: allowlist secret
    )

    assert "cannot require PostgreSQL" in "; ".join(database.configuration_errors)
    assert "requires memory checkpointing" in "; ".join(
        checkpoint.configuration_errors
    )
    assert "requires openai/gpt-oss-20b" in "; ".join(
        wrong_model.configuration_errors
    )
    assert "disables external web search" in "; ".join(
        web.configuration_errors
    )

    try:
        settings(OPENWEIGHT_HF_PROVIDER="auto")
    except ValueError as error:
        assert "explicit provider" in str(error)
    else:
        raise AssertionError("automatic provider selection was accepted")


def test_exact_public_origin_narrows_host_and_mcp_origin_configuration() -> None:
    public = settings(
        OPENWEIGHT_MCP_ENABLED="true",
        OPENWEIGHT_HF_PUBLIC_ORIGIN="https://owner-openweight.hf.space/",
        OPENWEIGHT_MCP_ALLOWED_HOSTS="*.hf.space:*",
        OPENWEIGHT_MCP_ALLOWED_ORIGINS="https://*.hf.space",
    )

    assert public.hf_public_origin == "https://owner-openweight.hf.space"
    assert public.mcp_allowed_hosts == ("owner-openweight.hf.space",)
    assert public.mcp_allowed_origins == ("https://owner-openweight.hf.space",)


@pytest.mark.parametrize(
    "origin",
    [
        "http://owner-openweight.hf.space",
        "https://*.hf.space",
        "https://user:secret@owner-openweight.hf.space",
        "https://localhost",
        "https://owner-openweight.hf.space/path",
    ],
)
def test_public_origin_rejects_unsafe_or_inexact_values(origin: str) -> None:
    with pytest.raises(ValueError, match="exact public HTTPS origin"):
        settings(OPENWEIGHT_HF_PUBLIC_ORIGIN=origin)


def test_readiness_validates_demo_dependencies_without_inference() -> None:
    runtime = DefaultPlatformRuntime(settings(), observability=observability())

    dependencies = runtime._readiness()
    states = {item.name: item for item in dependencies}

    assert runtime.inference_state == "unconfigured"
    assert states["model_backend"].required is False
    assert states["model_backend"].status == "unavailable"
    assert "no inference probe" in states["model_backend"].detail
    assert states["demo_retrieval"].status == "ready"
    assert states["demo_state"].status == "ready"
    assert "postgresql" not in states
    assert runtime._backend is None


def test_missing_token_policy_request_fails_before_retrieval_or_remote_call() -> None:
    runtime = DefaultPlatformRuntime(settings(), observability=observability())

    with pytest.raises(DependencyUnavailableError):
        runtime._query_policy("What controls privileged access?", request_id="r1")

    assert runtime.inference_state == "unconfigured"
    assert runtime._policy._store is None


def test_generic_agent_route_is_not_a_public_metered_gateway() -> None:
    runtime = DefaultPlatformRuntime(settings(HF_TOKEN="test-only"), observability=observability())

    with pytest.raises(InvalidRequestError):
        runtime._execute_query("Answer anything", request_id="r1")

    assert runtime.inference_state == "not_used"
    assert runtime._backend is None


def test_demo_operations_are_synthetic_isolated_and_fixed_shape() -> None:
    first = DefaultPlatformRuntime(settings(), observability=observability())
    second = DefaultPlatformRuntime(settings(), observability=observability())

    pending = first._lookup_record("lookup_access_request", "fic-req-002")
    proposal = first._propose_access_request_status(
        "fic-req-002", "approved", request_id="r2"
    )
    unchanged = first._lookup_record("lookup_access_request", "fic-req-002")
    outcome = first._resume_approval(
        proposal.approval_id,
        ApprovalDecision(decision="approve"),
        request_id="r4",
    )
    changed = first._lookup_record("lookup_access_request", "fic-req-002")
    reset = second._lookup_record("lookup_access_request", "fic-req-002")

    assert pending["approval_status"] == "pending"
    assert unchanged["approval_status"] == "pending"
    assert outcome.status == "approved"
    assert changed["approval_status"] == "approved"
    assert reset["approval_status"] == "pending"
