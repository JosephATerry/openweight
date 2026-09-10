from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from openweight_platform.api.app import REQUEST_ID_HEADER, create_app
from openweight_platform.api.config import ServiceSettings
from openweight_platform.api.contracts import DependencyStatus
from openweight_platform.api.logging import SafeJsonFormatter
from openweight_platform.api.observability import (
    Observability,
    SAFE_SPAN_ATTRIBUTES,
    create_observability,
    safe_span_attributes,
)
from openweight_platform.api.runtime import (
    AgentExecution,
    ApprovalOutcome,
    PendingApproval,
    _LazyOperationsTools,
    _LazyPolicyRetriever,
    _ObservedAccessRequestWriter,
    _ObservedBackend,
)
from openweight_platform.backends.base import GenerationResult, ModelBackend
from openweight_platform.operations.actions import AccessRequestStatusChange
from openweight_platform.orchestration.approval import (
    ActionProposal,
    ApprovalDecision,
)


def settings(**overrides: str) -> ServiceSettings:
    return ServiceSettings.from_env(
        {
            "OPENWEIGHT_DATABASE_REQUIRED": "false",
            "OPENWEIGHT_ENVIRONMENT": "test",
            "OPENWEIGHT_SERVICE_VERSION": "1.0.0-test",
            **overrides,
        }
    )


class FakeRuntime:
    backend_alias = "gpt-oss"
    inference_state = "not_initialized"

    def __init__(self) -> None:
        self.query_error: Exception | None = None
        self.closed = False

    async def readiness(self) -> list[DependencyStatus]:
        return [
            DependencyStatus(
                name="postgresql",
                status="ready",
                required=True,
                detail="reachable",
            )
        ]

    async def execute_query(
        self,
        question: str,
        *,
        request_id: str,
    ) -> AgentExecution:
        assert question and request_id
        if self.query_error is not None:
            raise self.query_error
        return AgentExecution(
            route="policy",
            routing_decision={"route": "policy"},
            result={"answer": "safe", "citations": []},
        )

    async def propose_access_request_status(
        self,
        access_request_id: str,
        new_status: str,
        *,
        request_id: str,
    ) -> PendingApproval:
        assert request_id
        return PendingApproval(
            approval_id="approval-safe",
            proposal=ActionProposal(
                action_type="set_access_request_status",
                summary="A validated status change is pending.",
                arguments={
                    "request_id": access_request_id,
                    "new_status": new_status,
                },
                consequence="The status changes only after approval.",
            ),
        )

    async def resume_approval(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        *,
        request_id: str,
    ) -> ApprovalOutcome:
        assert approval_id and request_id
        return ApprovalOutcome(
            approval_id=approval_id,
            status=("approved" if decision.decision == "approve" else "rejected"),
            action_result=None,
        )

    async def close(self) -> None:
        self.closed = True


async def _exercise(
    application: FastAPI,
    requests: list[tuple[str, str, dict[str, object]]],
) -> list[httpx.Response]:
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return [
                await client.request(method, path, **kwargs)
                for method, path, kwargs in requests
            ]


def _observability(*, tracing: bool = False) -> tuple[Observability, InMemorySpanExporter | None]:
    exporter = InMemorySpanExporter() if tracing else None
    telemetry = Observability(
        service_name="openweight-platform-test",
        environment="test",
        metrics_enabled=True,
        tracing_enabled=tracing,
        span_exporter=exporter,
        synchronous_tracing=True,
    )
    return telemetry, exporter


def test_metrics_endpoint_counts_template_routes_status_and_latency() -> None:
    telemetry, _ = _observability()
    application = create_app(
        settings=settings(),
        runtime=FakeRuntime(),
        observability=telemetry,
    )

    responses = asyncio.run(
        _exercise(
            application,
            [
                ("GET", "/healthz", {}),
                ("GET", "/does-not-exist/private-id", {}),
                ("GET", "/metrics", {}),
            ],
        )
    )
    metrics = telemetry.metrics_payload().decode("utf-8")

    assert [response.status_code for response in responses] == [200, 404, 200]
    assert "openweight_http_requests_total" in responses[-1].text
    assert (
        'openweight_http_requests_total{method="GET",route="/healthz",status_class="2xx"} 1.0'
        in metrics
    )
    assert (
        'openweight_http_requests_total{method="GET",route="unmatched",status_class="4xx"} 1.0'
        in metrics
    )
    assert "openweight_http_request_duration_seconds_bucket" in metrics
    assert 'openweight_http_requests_in_flight{method="GET"} 0.0' in metrics
    assert "private-id" not in metrics


def test_dynamic_ids_and_prompts_never_become_metric_labels() -> None:
    telemetry, _ = _observability()
    application = create_app(
        settings=settings(),
        runtime=FakeRuntime(),
        observability=telemetry,
    )
    marker = "never-export-this-prompt"
    dynamic_id = "access-request-private-984"

    responses = asyncio.run(
        _exercise(
            application,
            [
                (
                    "POST",
                    "/v1/agent/query",
                    {"json": {"question": marker}},
                ),
                (
                    "POST",
                    f"/v1/actions/access-requests/{dynamic_id}/proposals",
                    {"json": {"new_status": "approved"}},
                ),
            ],
        )
    )
    metrics = telemetry.metrics_payload().decode("utf-8")

    assert [response.status_code for response in responses] == [200, 202]
    assert marker not in metrics
    assert dynamic_id not in metrics
    assert (
        'route="/v1/actions/access-requests/{access_request_id}/proposals"'
        in metrics
    )
    assert 'openweight_agent_requests_total{backend="gpt-oss",result="success"} 1.0' in metrics
    assert 'openweight_approval_proposals_total{result="success"} 1.0' in metrics
    assert 'openweight_approval_duration_seconds_count{operation="proposal"} 1.0' in metrics


def test_dynamic_route_ids_do_not_enter_structured_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dynamic_id = "access-request-log-private-22"
    application = create_app(settings=settings(), runtime=FakeRuntime())

    response = asyncio.run(
        _exercise(
            application,
            [
                (
                    "POST",
                    f"/v1/actions/access-requests/{dynamic_id}/proposals",
                    {"json": {"new_status": "approved"}},
                )
            ],
        )
    )[0]
    logs = "\n".join(
        SafeJsonFormatter().format(record)
        for record in caplog.records
        if record.name == "openweight_platform.api.access"
    )

    assert response.status_code == 202
    assert dynamic_id not in logs
    assert (
        "/v1/actions/access-requests/{access_request_id}/proposals" in logs
    )


def test_approval_resume_and_safe_error_counters_are_bounded() -> None:
    telemetry, _ = _observability()
    runtime = FakeRuntime()
    application = create_app(
        settings=settings(),
        runtime=runtime,
        observability=telemetry,
    )

    rejected = asyncio.run(
        _exercise(
            application,
            [
                (
                    "POST",
                    "/v1/approvals/approval-secret/resume",
                    {"json": {"decision": "reject", "comment": "private"}},
                )
            ],
        )
    )[0]
    runtime.query_error = RuntimeError("password=hunter2")
    failed = asyncio.run(
        _exercise(
            application,
            [
                (
                    "POST",
                    "/v1/agent/query",
                    {"json": {"question": "private prompt"}},
                )
            ],
        )
    )[0]
    metrics = telemetry.metrics_payload().decode("utf-8")

    assert rejected.status_code == 200
    assert failed.status_code == 500
    assert 'openweight_approval_resumes_total{decision="reject",result="rejected"} 1.0' in metrics
    assert 'openweight_approval_duration_seconds_count{operation="resume"} 1.0' in metrics
    assert 'openweight_errors_total{error_type="internal_error",operation="agent"} 1.0' in metrics
    assert "approval-secret" not in metrics
    assert "hunter2" not in metrics
    assert "private prompt" not in metrics


def test_metrics_route_is_hidden_and_can_be_disabled() -> None:
    disabled_settings = settings(OPENWEIGHT_METRICS_ENABLED="false")
    application = create_app(
        settings=disabled_settings,
        runtime=FakeRuntime(),
    )
    response = asyncio.run(
        _exercise(application, [("GET", "/metrics", {})])
    )[0]

    assert response.status_code == 404
    assert "/metrics" not in application.openapi()["paths"]

    enabled = create_app(settings=settings(), runtime=FakeRuntime())
    assert "/metrics" not in enabled.openapi()["paths"]


class FakeBackend(ModelBackend):
    @property
    def model_name(self) -> str:
        return "fake"

    def load(self) -> None:
        return None

    def generate(self, prompt: str) -> GenerationResult:
        assert prompt
        return GenerationResult(
            text="safe",
            input_tokens=1,
            output_tokens=1,
            generation_seconds=0.01,
            peak_vram_gib=0.0,
        )

    def unload(self) -> None:
        return None


@dataclass
class FakeWriter:
    calls: int = 0

    def set_access_request_status_once(
        self,
        effect_id: str,
        request_id: str,
        new_status: str,
    ) -> AccessRequestStatusChange:
        assert effect_id
        self.calls += 1
        return AccessRequestStatusChange(
            request_id=request_id,
            previous_status="pending",
            new_status=new_status,  # type: ignore[arg-type]
            changed=True,
        )


def test_backend_retrieval_tool_and_controlled_write_boundaries_are_instrumented() -> None:
    telemetry, _ = _observability()
    backend = _ObservedBackend(FakeBackend(), "gpt-oss", telemetry)
    writer = FakeWriter()
    observed_writer = _ObservedAccessRequestWriter(writer, telemetry)

    backend.generate("sensitive prompt not exported")
    observed_writer.set_access_request_status_once(
        "approval-private",
        "private-request",
        "approved",
    )
    with pytest.raises(Exception):
        _LazyPolicyRetriever(settings(), telemetry)("private retrieval")
    tools = _LazyOperationsTools(settings(), telemetry).build()
    with pytest.raises(Exception):
        tools[0].invoke({"identifier": "private-person"})
    metrics = telemetry.metrics_payload().decode("utf-8")

    assert (
        'openweight_backend_calls_total{backend="gpt-oss",result="success"} 1.0'
        in metrics
    )
    assert (
        'openweight_retrieval_operations_total{result="error",retriever="policy"} 1.0'
        in metrics
    )
    assert 'openweight_tool_calls_total{result="error",tool_name="get_employee_record"} 1.0' in metrics
    assert 'openweight_controlled_writes_total{action_type="set_access_request_status",result="success"} 1.0' in metrics
    assert 'openweight_controlled_write_duration_seconds_count{action_type="set_access_request_status"} 1.0' in metrics
    assert writer.calls == 1
    assert "sensitive prompt" not in metrics
    assert "private-person" not in metrics


def test_traces_use_allowlisted_attributes_and_never_capture_content() -> None:
    telemetry, exporter = _observability(tracing=True)
    assert exporter is not None
    application = create_app(
        settings=settings(),
        runtime=FakeRuntime(),
        observability=telemetry,
    )
    marker = "trace-private-prompt"

    response = asyncio.run(
        _exercise(
            application,
            [
                (
                    "POST",
                    "/v1/agent/query",
                    {
                        "json": {"question": marker},
                        "headers": {REQUEST_ID_HEADER: "trace-request-1"},
                    },
                )
            ],
        )
    )[0]
    spans = exporter.get_finished_spans()
    serialized = json.dumps(
        [
            {
                "name": span.name,
                "attributes": dict(span.attributes or {}),
                "events": [event.name for event in span.events],
            }
            for span in spans
        ],
        sort_keys=True,
    )

    assert response.status_code == 200
    assert {span.name for span in spans} >= {"http.request", "agent.execute"}
    assert all(set(span.attributes or {}).issubset(SAFE_SPAN_ATTRIBUTES) for span in spans)
    http_span = next(span for span in spans if span.name == "http.request")
    assert http_span.attributes["http.route"] == "/v1/agent/query"
    assert marker not in serialized
    assert "trace-request-1" in serialized
    assert all(not span.events for span in spans)


def test_trace_attribute_allowlist_rejects_payload_fields() -> None:
    for unsafe in (
        "prompt",
        "response.text",
        "reasoning_content",
        "db.statement",
        "authorization",
    ):
        with pytest.raises(ValueError, match="Unsafe trace attribute"):
            safe_span_attributes({unsafe: "private"})


def test_trace_errors_export_only_fixed_classification() -> None:
    telemetry, exporter = _observability(tracing=True)
    assert exporter is not None

    with pytest.raises(RuntimeError, match="private exception"):
        with telemetry.span(
            "backend.generate",
            {
                "openweight.operation": "backend",
                "openweight.backend": "gpt-oss",
            },
            error_type="backend_unavailable",
        ):
            raise RuntimeError("private exception with password=hunter2")
    telemetry.shutdown()
    spans = exporter.get_finished_spans()
    serialized = json.dumps(
        [dict(span.attributes or {}) for span in spans],
        sort_keys=True,
    )

    assert len(spans) == 1
    assert spans[0].attributes["error.type"] == "backend_unavailable"
    assert spans[0].status.status_code.name == "ERROR"
    assert spans[0].events == ()
    assert "private exception" not in serialized
    assert "hunter2" not in serialized


def test_tracing_is_noop_by_default_and_configuration_is_safe() -> None:
    default_settings = settings()
    telemetry = create_observability(default_settings)

    with telemetry.span(
        "agent.execute",
        {"openweight.operation": "agent"},
    ):
        pass

    assert default_settings.observability_enabled is True
    assert default_settings.metrics_enabled is True
    assert default_settings.tracing_enabled is False
    assert default_settings.otlp_endpoint is None
    assert telemetry.trace_context().trace_id is None

    configured = settings(
        OPENWEIGHT_TRACING_ENABLED="true",
        OPENWEIGHT_OTLP_ENDPOINT="https://collector.example/v1/traces",
    )
    assert configured.configuration_errors == ()
    assert "collector.example" not in repr(configured)

    missing = settings(OPENWEIGHT_TRACING_ENABLED="true")
    assert missing.configuration_errors == (
        "tracing is enabled but OPENWEIGHT_OTLP_ENDPOINT is missing",
    )


def test_structured_logging_keeps_correlation_and_drops_payloads() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request_completed",
        args=(),
        exc_info=None,
    )
    record.request_id = "request-safe"
    record.trace_id = "0" * 32
    record.span_id = "1" * 16
    record.operation = "agent"
    record.raw_prompt = "never-log"
    record.reasoning_content = "never-log-either"

    payload = json.loads(SafeJsonFormatter().format(record))

    assert payload["request_id"] == "request-safe"
    assert payload["trace_id"] == "0" * 32
    assert payload["span_id"] == "1" * 16
    assert payload["operation"] == "agent"
    assert "never-log" not in json.dumps(payload)


def test_api_import_does_not_import_model_frameworks() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import openweight_platform.api.app; "
                "assert 'torch' not in sys.modules; "
                "assert 'transformers' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": "src", "OPENWEIGHT_DATABASE_REQUIRED": "false"},
    )

    assert completed.returncode == 0, completed.stderr
