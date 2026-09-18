from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from openweight_platform.api.app import REQUEST_ID_HEADER, create_app
from openweight_platform.api.config import ServiceSettings
from openweight_platform.api.contracts import DependencyStatus
from openweight_platform.api.errors import (
    DependencyUnavailableError,
    InvalidRequestError,
    ResourceNotFoundError,
    StateConflictError,
)
from openweight_platform.api.logging import SafeJsonFormatter
from openweight_platform.api.observability import Observability
from openweight_platform.api.run import main as run_api
from openweight_platform.api.runtime import (
    AgentExecution,
    ApprovalCoordinator,
    ApprovalOutcome,
    DefaultPlatformRuntime,
    GroundedPolicyAnswer,
    PendingApproval,
    PolicyEvidence,
    PolicyStreamEvent,
)
from openweight_platform.operations.actions import AccessRequestStatusChange
from openweight_platform.operations.models import AccessRequest
from openweight_platform.orchestration.approval import (
    ActionProposal,
    ApprovalDecision,
)


def service_settings(**overrides: str) -> ServiceSettings:
    values = {
        "OPENWEIGHT_DATABASE_REQUIRED": "false",
        "OPENWEIGHT_ENVIRONMENT": "test",
        "OPENWEIGHT_SERVICE_VERSION": "9.8.7",
        "OPENWEIGHT_BUILD_SHA": "injected-build",
        **overrides,
    }
    return ServiceSettings.from_env(values)


class FakeRuntime:
    def __init__(self) -> None:
        self.backend_alias = "fake-backend"
        self.inference_state = "not_initialized"
        self.dependencies = [
            DependencyStatus(
                name="fake",
                status="ready",
                required=True,
                detail="available",
            )
        ]
        self.query_error: Exception | None = None
        self.query_result = AgentExecution(
            route="policy",
            routing_decision={"route": "policy", "confidence": 0.9},
            result={"answer": "Policy evidence is available.", "citations": []},
        )
        self.policy_error: Exception | None = None
        self.policy_result = GroundedPolicyAnswer(
            status="answered",
            answer=(
                "Privileged access requires explicit approval "
                "[EPG-ACCESS-001#1]."
            ),
            citations=["[EPG-ACCESS-001#1]"],
            citation_valid=True,
            evidence=[
                PolicyEvidence(
                    citation_id="[EPG-ACCESS-001#1]",
                    policy_id="EPG-ACCESS-001",
                    title="Privileged Infrastructure Access Policy",
                    domain="infrastructure-access",
                    chunk_index=1,
                    content="Privileged access requires explicit approval.",
                )
            ],
        )
        self.closed = False
        self.proposal_error: Exception | None = None
        self.resume_error: Exception | None = None
        self.request_ids: list[str] = []
        self.resumes: list[tuple[str, ApprovalDecision]] = []

    async def readiness(self) -> list[DependencyStatus]:
        return self.dependencies

    async def execute_query(
        self,
        question: str,
        *,
        request_id: str,
    ) -> AgentExecution:
        assert question
        self.request_ids.append(request_id)
        if self.query_error is not None:
            raise self.query_error
        return self.query_result

    async def query_policy(
        self,
        question: str,
        *,
        request_id: str,
    ) -> GroundedPolicyAnswer:
        assert question
        self.request_ids.append(request_id)
        if self.policy_error is not None:
            raise self.policy_error
        return self.policy_result

    async def stream_policy(
        self,
        question: str,
        *,
        request_id: str,
    ):
        assert question
        self.request_ids.append(request_id)
        if self.policy_error is not None:
            raise self.policy_error
        yield PolicyStreamEvent(kind="stage", value="searching")
        yield PolicyStreamEvent(kind="stage", value="generating")
        yield PolicyStreamEvent(
            kind="delta",
            value="Privileged access requires explicit approval ",
        )
        yield PolicyStreamEvent(kind="complete", result=self.policy_result)

    async def propose_access_request_status(
        self,
        access_request_id: str,
        new_status: str,
        *,
        request_id: str,
    ) -> PendingApproval:
        self.request_ids.append(request_id)
        if self.proposal_error is not None:
            raise self.proposal_error
        return PendingApproval(
            approval_id="approval-123",
            proposal=ActionProposal(
                action_type="set_access_request_status",
                summary=(
                    f"Set access request {access_request_id} to {new_status}."
                ),
                arguments={
                    "request_id": access_request_id,
                    "new_status": new_status,
                },
                consequence="The approval status will change if approved.",
            ),
        )

    async def resume_approval(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        *,
        request_id: str,
    ) -> ApprovalOutcome:
        self.request_ids.append(request_id)
        if self.resume_error is not None:
            raise self.resume_error
        self.resumes.append((approval_id, decision))
        return ApprovalOutcome(
            approval_id=approval_id,
            status="approved" if decision.decision == "approve" else "rejected",
            action_result=(
                {"request_id": "req-1", "changed": True}
                if decision.decision == "approve"
                else None
            ),
        )

    async def close(self) -> None:
        self.closed = True


def request(
    application: FastAPI,
    method: str,
    path: str,
    **kwargs: object,
) -> httpx.Response:
    async def perform() -> httpx.Response:
        async with application.router.lifespan_context(application):
            transport = httpx.ASGITransport(
                app=application,
                raise_app_exceptions=False,
            )
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.request(method, path, **kwargs)

    return asyncio.run(perform())


def app_for(runtime: FakeRuntime) -> FastAPI:
    return create_app(settings=service_settings(), runtime=runtime)


def test_health_is_lightweight_and_request_id_is_propagated() -> None:
    runtime = FakeRuntime()
    response = request(
        app_for(runtime),
        "GET",
        "/healthz",
        headers={REQUEST_ID_HEADER: "portfolio-request-42"},
    )

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "portfolio-request-42"
    assert response.json() == {
        "request_id": "portfolio-request-42",
        "status": "alive",
        "service": "openweight-platform",
    }
    assert runtime.request_ids == []
    assert runtime.closed is True


def test_invalid_client_request_id_is_replaced() -> None:
    runtime = FakeRuntime()
    response = request(
        app_for(runtime),
        "GET",
        "/healthz",
        headers={REQUEST_ID_HEADER: "contains spaces and is invalid"},
    )

    generated = response.headers[REQUEST_ID_HEADER]
    assert generated != "contains spaces and is invalid"
    assert response.json()["request_id"] == generated
    assert len(generated) == 32


def test_readiness_reports_success_and_required_dependency_failure() -> None:
    runtime = FakeRuntime()
    application = app_for(runtime)
    ready = request(application, "GET", "/readyz")
    runtime.dependencies = [
        DependencyStatus(
            name="postgresql",
            status="unavailable",
            required=True,
            detail="connection probe failed",
        ),
        DependencyStatus(
            name="web_search",
            status="disabled",
            required=False,
            detail="optional integration disabled",
        ),
    ]
    unavailable = request(application, "GET", "/readyz")

    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert unavailable.status_code == 503
    assert unavailable.json()["status"] == "not_ready"
    assert "password" not in unavailable.text.lower()


def test_cors_accepts_only_the_configured_frontend_origin() -> None:
    settings = service_settings(
        OPENWEIGHT_CORS_ALLOWED_ORIGINS="https://frontend.example.test",
    )
    application = create_app(settings=settings, runtime=FakeRuntime())
    headers = {
        "Origin": "https://frontend.example.test",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-request-id",
    }

    accepted = request(application, "OPTIONS", "/v1/policy/query/stream", headers=headers)
    rejected = request(
        application,
        "OPTIONS",
        "/v1/policy/query/stream",
        headers={**headers, "Origin": "https://unknown.example.test"},
    )

    assert accepted.status_code == 200
    assert accepted.headers["access-control-allow-origin"] == headers["Origin"]
    assert accepted.headers.get("access-control-allow-credentials") is None
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers
    assert "*" not in accepted.headers["access-control-allow-origin"]


@pytest.mark.parametrize(
    "value",
    (
        "*",
        "https://*.example.test",
        "https://frontend.example.test/path",
        "https://user:password@example.test",  # pragma: allowlist secret
    ),
)
def test_cors_configuration_rejects_non_exact_origins(value: str) -> None:
    with pytest.raises(ValueError, match=r"exact HTTP\(S\) origins"):
        service_settings(OPENWEIGHT_CORS_ALLOWED_ORIGINS=value)


def test_policy_encoder_warmup_is_single_concurrent_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = DefaultPlatformRuntime(
        service_settings(),
        observability=Observability(
            service_name="openweight-test",
            environment="test",
            metrics_enabled=False,
            tracing_enabled=False,
        ),
    )
    calls = 0

    def warmup() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(runtime._policy, "warmup", warmup)
    async def warm_concurrently() -> list[bool]:
        return await asyncio.gather(
            runtime.warm_policy_retrieval(),
            runtime.warm_policy_retrieval(),
        )

    results = asyncio.run(warm_concurrently())

    assert results == [True, True]
    assert calls == 1
    assert runtime._policy_encoder_readiness().status == "ready"
    assert runtime._backend is None

    failed = DefaultPlatformRuntime(
        service_settings(),
        observability=runtime._observability,
    )

    def fail_warmup() -> None:
        raise RuntimeError("sensitive model failure")

    monkeypatch.setattr(failed._policy, "warmup", fail_warmup)
    assert asyncio.run(failed.warm_policy_retrieval()) is False
    status = failed._policy_encoder_readiness()
    assert status.status == "unavailable"
    assert status.detail == "local retrieval encoder unavailable"
    assert "sensitive" not in status.detail
    assert failed._backend is None


def test_configuration_is_environment_driven_and_secret_repr_is_redacted() -> None:
    settings = service_settings(
        OPENWEIGHT_DATABASE_REQUIRED="true",
        POSTGRES_DB="portfolio",
        POSTGRES_USER="service",
        POSTGRES_PASSWORD="do-not-display",
        POSTGRES_HOST="database.internal",
        POSTGRES_PORT="5433",
        OPENWEIGHT_WEB_ENABLED="true",
        TAVILY_API_KEY="configured-but-secret",
        MLFLOW_TRACKING_URI="http://mlflow.internal",
    )

    assert settings.configuration_errors == ()
    assert settings.database_config is not None
    assert settings.database_config.connect_kwargs["port"] == 5433
    assert settings.web_enabled is True
    assert settings.mlflow_configured is True
    assert "do-not-display" not in repr(settings)
    assert "configured-but-secret" not in repr(settings)


def test_missing_required_configuration_is_reported_without_secret_data() -> None:
    settings = ServiceSettings.from_env(
        {
            "OPENWEIGHT_DATABASE_REQUIRED": "true",
            "OPENWEIGHT_WEB_ENABLED": "true",
        }
    )

    assert settings.database_config is None
    assert settings.configuration_errors == (
        "required database configuration is missing",
        "web search is enabled but TAVILY_API_KEY is missing",
    )


def test_canonical_launcher_uses_environment_bind_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(application: str, **kwargs: object) -> None:
        captured["application"] = application
        captured.update(kwargs)

    monkeypatch.setenv("OPENWEIGHT_DATABASE_REQUIRED", "false")
    monkeypatch.setenv("OPENWEIGHT_API_HOST", "127.0.0.9")
    monkeypatch.setenv("OPENWEIGHT_API_PORT", "8123")
    monkeypatch.setattr(uvicorn, "run", fake_run)

    run_api()

    assert captured == {
        "application": "openweight_platform.api.app:app",
        "host": "127.0.0.9",
        "port": 8123,
        "log_level": "info",
        "access_log": False,
        "reload": False,
    }


def test_service_info_exposes_only_injected_safe_metadata() -> None:
    runtime = FakeRuntime()
    response = request(app_for(runtime), "GET", "/v1/service-info")

    assert response.status_code == 200
    assert response.json() | {"request_id": "ignored"} == {
        "request_id": "ignored",
        "service": "openweight-platform",
        "version": "9.8.7",
        "environment": "test",
        "deployment_profile": "local",
        "backend": "fake-backend",
        "model_id": "openai/gpt-oss-20b",
        "inference_provider": "local_transformers",
        "inference_configured": True,
        "inference_state": "not_initialized",
        "demo_state": "durable",
        "build_sha": "injected-build",
    }
    assert "/home/" not in response.text


def test_valid_agent_request_has_typed_safe_response() -> None:
    runtime = FakeRuntime()
    response = request(
        app_for(runtime),
        "POST",
        "/v1/agent/query",
        json={"question": "Which policy evidence applies?"},
        headers={REQUEST_ID_HEADER: "agent-1"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "request_id": "agent-1",
        "status": "completed",
        "route": "policy",
        "routing_decision": {"route": "policy", "confidence": 0.9},
        "result": {
            "answer": "Policy evidence is available.",
            "citations": [],
        },
    }
    assert runtime.request_ids == ["agent-1"]


def test_policy_query_returns_typed_grounded_answer_and_safe_evidence() -> None:
    runtime = FakeRuntime()
    response = request(
        app_for(runtime),
        "POST",
        "/v1/policy/query",
        json={"question": "What controls privileged access?"},
        headers={REQUEST_ID_HEADER: "policy-1"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "request_id": "policy-1",
        "status": "answered",
        "source_scope": "internal_policy",
        "answer": (
            "Privileged access requires explicit approval "
            "[EPG-ACCESS-001#1]."
        ),
        "citations": ["[EPG-ACCESS-001#1]"],
        "citation_valid": True,
        "evidence": [
            {
                "citation_id": "[EPG-ACCESS-001#1]",
                "policy_id": "EPG-ACCESS-001",
                "title": "Privileged Infrastructure Access Policy",
                "domain": "infrastructure-access",
                "chunk_index": 1,
                "content": "Privileged access requires explicit approval.",
            }
        ],
    }
    assert "source_path" not in response.text
    assert "prompt" not in response.text
    assert runtime.request_ids == ["policy-1"]


def test_policy_query_returns_typed_insufficient_evidence_result() -> None:
    runtime = FakeRuntime()
    runtime.policy_result = GroundedPolicyAnswer(
        status="insufficient_evidence",
        answer=(
            "OpenWeight could not find sufficient internal policy evidence "
            "to answer this question confidently."
        ),
        citations=[],
        citation_valid=False,
        evidence=[],
    )

    response = request(
        app_for(runtime),
        "POST",
        "/v1/policy/query",
        json={"question": "What unsupported policy applies?"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "insufficient_evidence"
    assert response.json()["citations"] == []
    assert response.json()["evidence"] == []


def test_policy_backend_failure_maps_to_safe_503() -> None:
    runtime = FakeRuntime()
    runtime.policy_error = DependencyUnavailableError("postgres://secret")

    response = request(
        app_for(runtime),
        "POST",
        "/v1/policy/query",
        json={"question": "What controls privileged access?"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"
    assert "secret" not in response.text


def test_policy_stream_emits_stages_final_text_and_authoritative_result() -> None:
    runtime = FakeRuntime()
    response = request(
        app_for(runtime),
        "POST",
        "/v1/policy/query/stream",
        json={"question": "What controls privileged access?"},
        headers={REQUEST_ID_HEADER: "policy-stream-1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.index('"stage":"searching"') < response.text.index(
        '"stage":"generating"'
    )
    assert "event: answer_delta" in response.text
    assert "event: complete" in response.text
    assert '"citation_valid":true' in response.text
    assert '"policy_id":"EPG-ACCESS-001"' in response.text
    assert "reasoning" not in response.text
    assert "source_path" not in response.text
    assert runtime.request_ids == ["policy-stream-1"]


def test_policy_stream_failure_is_sanitized_after_headers_start() -> None:
    runtime = FakeRuntime()
    runtime.policy_error = DependencyUnavailableError(
        "password=private streaming failure"
    )
    response = request(
        app_for(runtime),
        "POST",
        "/v1/policy/query/stream",
        json={"question": "What controls privileged access?"},
    )

    assert response.status_code == 200
    assert "event: error" in response.text
    assert '"code":"dependency_unavailable"' in response.text
    assert "private streaming failure" not in response.text


def test_huggingface_profile_rate_limits_only_metered_policy_requests() -> None:
    runtime = FakeRuntime()
    application = create_app(
        settings=service_settings(
            OPENWEIGHT_DEPLOYMENT_PROFILE="huggingface",
            OPENWEIGHT_CHECKPOINT_BACKEND="memory",
            OPENWEIGHT_WEB_ENABLED="false",
            OPENWEIGHT_HF_RATE_LIMIT_REQUESTS="1",
        ),
        runtime=runtime,
    )

    assert request(application, "GET", "/healthz").status_code == 200
    assert request(application, "GET", "/readyz").status_code == 200
    first = request(
        application,
        "POST",
        "/v1/policy/query",
        json={"question": "What controls privileged access?"},
    )
    throttled = request(
        application,
        "POST",
        "/v1/policy/query",
        json={"question": "What controls privileged access?"},
    )

    assert first.status_code == 200
    assert throttled.status_code == 429
    assert throttled.json()["error"] == {
        "code": "public_demo_busy",
        "message": (
            "The public demo is receiving several requests. "
            "Please try again shortly."
        ),
    }
    assert len(runtime.request_ids) == 1


def test_local_profile_does_not_apply_public_inference_limits() -> None:
    runtime = FakeRuntime()
    application = create_app(
        settings=service_settings(OPENWEIGHT_HF_RATE_LIMIT_REQUESTS="1"),
        runtime=runtime,
    )

    responses = [
        request(
            application,
            "POST",
            "/v1/policy/query",
            json={"question": "What controls privileged access?"},
        )
        for _ in range(2)
    ]

    assert [response.status_code for response in responses] == [200, 200]
    assert len(runtime.request_ids) == 2


def test_huggingface_stream_releases_concurrency_slot_after_completion() -> None:
    application = create_app(
        settings=service_settings(
            OPENWEIGHT_DEPLOYMENT_PROFILE="huggingface",
            OPENWEIGHT_CHECKPOINT_BACKEND="memory",
            OPENWEIGHT_WEB_ENABLED="false",
            OPENWEIGHT_HF_INFERENCE_CONCURRENCY_LIMIT="1",
        ),
        runtime=FakeRuntime(),
    )

    responses = [
        request(
            application,
            "POST",
            "/v1/policy/query/stream",
            json={"question": "What controls privileged access?"},
        )
        for _ in range(2)
    ]

    assert [response.status_code for response in responses] == [200, 200]
    assert application.state.public_demo_inference_gate.active_count == 0


def test_exact_public_origin_enforces_whole_application_host() -> None:
    application = create_app(
        settings=service_settings(
            OPENWEIGHT_DEPLOYMENT_PROFILE="huggingface",
            OPENWEIGHT_CHECKPOINT_BACKEND="memory",
            OPENWEIGHT_WEB_ENABLED="false",
            OPENWEIGHT_HF_PUBLIC_ORIGIN="https://owner-openweight.hf.space",
        ),
        runtime=FakeRuntime(),
    )

    accepted = request(
        application,
        "GET",
        "/healthz",
        headers={"Host": "owner-openweight.hf.space"},
    )
    rejected = request(
        application,
        "GET",
        "/healthz",
        headers={"Host": "attacker.example"},
    )

    assert accepted.status_code == 200
    assert rejected.status_code == 400


def test_policy_stream_and_json_clean_source_footer_after_citation_validation() -> None:
    class SourceSyntaxRuntime(FakeRuntime):
        async def stream_policy(self, question, *, request_id):
            for chunk in ("Use **approval**.\n\n[SOU", "RCE [EPG-ACCESS-001#1]]"):
                yield PolicyStreamEvent(kind="delta", value=chunk)
            yield PolicyStreamEvent(kind="complete", result=self.policy_result)

    runtime = SourceSyntaxRuntime()
    runtime.policy_result = GroundedPolicyAnswer(
        status="answered",
        answer="Use **approval**.\n\n[SOURCE [EPG-ACCESS-001#1]]",
        citations=["[EPG-ACCESS-001#1]"], citation_valid=True, evidence=[],
    )
    for path in ("/v1/policy/query", "/v1/policy/query/stream"):
        response = request(app_for(runtime), "POST", path, json={"question": "What controls access?"})
        assert response.status_code == 200
        assert "SOURCE" not in response.text
        assert "[SOU" not in response.text
        assert "Use **approval**." in response.text
        assert "EPG-ACCESS-001#1" in response.text
    assert "SOURCE" in runtime.policy_result.answer


@pytest.mark.parametrize(
    "payload",
    [{}, {"question": ""}, {"question": "   "}, {"question": "ok", "x": 1}],
)
def test_invalid_agent_request_uses_safe_422(payload: dict[str, object]) -> None:
    runtime = FakeRuntime()
    response = request(
        app_for(runtime),
        "POST",
        "/v1/agent/query",
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "validation_error",
        "message": "The request does not match the required schema.",
    }
    assert "input" not in response.text


def test_backend_failure_maps_to_safe_503() -> None:
    runtime = FakeRuntime()
    runtime.query_error = DependencyUnavailableError("postgres://secret")
    response = request(
        app_for(runtime),
        "POST",
        "/v1/agent/query",
        json={"question": "safe question"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"
    assert "secret" not in response.text


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (InvalidRequestError(), 400, "invalid_request"),
        (ResourceNotFoundError(), 404, "not_found"),
        (StateConflictError(), 409, "state_conflict"),
    ],
)
def test_operational_errors_have_stable_safe_statuses(
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    runtime = FakeRuntime()
    runtime.proposal_error = error
    response = request(
        app_for(runtime),
        "POST",
        "/v1/actions/access-requests/req-1/proposals",
        json={"new_status": "approved"},
    )

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code


def test_unexpected_error_and_hidden_reasoning_are_not_exposed() -> None:
    runtime = FakeRuntime()
    runtime.query_error = RuntimeError("password=hunter2 raw prompt")
    application = app_for(runtime)
    failed = request(
        application,
        "POST",
        "/v1/agent/query",
        json={"question": "safe question"},
    )
    runtime.query_error = None
    runtime.query_result = AgentExecution(
        route="policy",
        routing_decision={"route": "policy"},
        result={"reasoning_content": "private scratchpad"},
    )
    unsafe = request(
        application,
        "POST",
        "/v1/agent/query",
        json={"question": "another safe question"},
    )

    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "internal_error"
    assert "hunter2" not in failed.text
    assert unsafe.status_code == 500
    assert unsafe.json()["error"]["code"] == "unsafe_internal_result"
    assert "scratchpad" not in unsafe.text


def test_approval_routes_expose_proposal_then_explicit_resume() -> None:
    runtime = FakeRuntime()
    application = app_for(runtime)
    proposed = request(
        application,
        "POST",
        "/v1/actions/access-requests/req-1/proposals",
        json={"new_status": "approved"},
        headers={REQUEST_ID_HEADER: "proposal-1"},
    )
    rejected = request(
        application,
        "POST",
        "/v1/approvals/approval-123/resume",
        json={"decision": "reject", "comment": "Not authorized."},
        headers={REQUEST_ID_HEADER: "resume-1"},
    )

    assert proposed.status_code == 202
    assert proposed.json()["status"] == "approval_required"
    assert proposed.json()["approval_required"] is True
    assert proposed.json()["proposal"]["action_type"] == (
        "set_access_request_status"
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["action_result"] is None
    assert runtime.resumes[0][1].decision == "reject"


def test_approved_resume_returns_only_controlled_action_result() -> None:
    runtime = FakeRuntime()
    response = request(
        app_for(runtime),
        "POST",
        "/v1/approvals/approval-123/resume",
        json={"decision": "approve"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["action_result"] == {
        "request_id": "req-1",
        "changed": True,
    }


@dataclass
class FakeAccessRequestReader:
    record: AccessRequest | None

    def lookup_access_request(self, request_id: str) -> AccessRequest | None:
        if self.record is not None and self.record.request_id == request_id:
            return self.record
        return None


class FakeAccessRequestWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def set_access_request_status_once(
        self,
        effect_id: str,
        request_id: str,
        new_status: str,
    ) -> AccessRequestStatusChange:
        assert effect_id.startswith("approval-")
        self.calls.append((request_id, new_status))
        return AccessRequestStatusChange(
            request_id=request_id,
            previous_status="pending",
            new_status=new_status,  # type: ignore[arg-type]
            changed=True,
        )


def test_real_approval_coordinator_blocks_duplicate_write_and_invalid_state() -> None:
    record = AccessRequest(
        request_id="req-1",
        subject_type="employee",
        subject_id="employee-1",
        system_name="finance",
        requested_role="reader",
        approval_status="pending",
        requested_start_date=date(2026, 1, 1),
        requested_end_date=None,
    )
    writer = FakeAccessRequestWriter()
    coordinator = ApprovalCoordinator(FakeAccessRequestReader(record), writer)

    pending = coordinator.propose("req-1", "approved")
    with pytest.raises(StateConflictError):
        coordinator.propose("req-1", "denied")
    outcome = coordinator.resume(
        pending.approval_id,
        ApprovalDecision(decision="approve"),
    )
    with pytest.raises(StateConflictError):
        coordinator.resume(
            pending.approval_id,
            ApprovalDecision(decision="approve"),
        )

    assert outcome.status == "approved"
    assert writer.calls == [("req-1", "approved")]


def test_default_runtime_startup_does_not_initialize_backend() -> None:
    application = create_app(settings=service_settings())

    async def check() -> httpx.Response:
        async with application.router.lifespan_context(application):
            runtime = application.state.runtime
            assert isinstance(runtime, DefaultPlatformRuntime)
            assert runtime._backend is None
            assert runtime._graph is None
            transport = httpx.ASGITransport(app=application)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.get("/healthz")

    response = asyncio.run(check())

    assert response.status_code == 200


def test_openapi_documents_narrow_surface_without_arbitrary_write_routes() -> None:
    runtime = FakeRuntime()
    schema = app_for(runtime).openapi()

    paths = set(schema["paths"])
    assert paths == {
        "/healthz",
        "/readyz",
        "/v1/service-info",
        "/v1/agent/query",
        "/v1/policy/query",
        "/v1/policy/query/stream",
        "/v1/access-requests",
        "/v1/access-requests/{access_request_id}",
        "/v1/actions/access-requests/{access_request_id}/proposals",
        "/v1/approvals/{approval_id}/resume",
    }
    assert not any(
        forbidden in path
        for path in paths
        for forbidden in ("sql", "training", "evaluation", "holdout", "tool")
    )


def test_structured_log_formatter_ignores_non_allowlisted_sensitive_fields() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request_completed",
        args=(),
        exc_info=None,
    )
    record.request_id = "safe-id"
    record.status_code = 200
    record.raw_prompt = "private prompt"
    record.password = "do-not-log"

    payload = json.loads(SafeJsonFormatter().format(record))

    assert payload["event"] == "request_completed"
    assert payload["request_id"] == "safe-id"
    assert payload["status_code"] == 200
    assert "raw_prompt" not in payload
    assert "password" not in payload
    assert "private prompt" not in json.dumps(payload)
