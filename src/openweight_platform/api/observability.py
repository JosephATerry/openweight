"""Privacy-safe metrics and tracing for the HTTP service runtime."""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace import Span, Status, StatusCode, Tracer
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from openweight_platform.api.config import ServiceSettings


SAFE_HTTP_ROUTES: Final = frozenset(
    {
        "/healthz",
        "/readyz",
        "/metrics",
        "/mcp",
        "/",
        "/overview",
        "/assistant",
        "/access-requests",
        "/approvals",
        "/system",
        "/v1/service-info",
        "/v1/agent/query",
        "/v1/policy/query",
        "/v1/policy/query/stream",
        "/v1/access-requests",
        "/v1/access-requests/{access_request_id}",
        "/v1/actions/access-requests/{access_request_id}/proposals",
        "/v1/approvals/{approval_id}/resume",
        "unmatched",
    }
)
SAFE_SPAN_ATTRIBUTES: Final = frozenset(
    {
        "deployment.environment.name",
        "error.type",
        "http.request.method",
        "http.response.status_code",
        "http.route",
        "openweight.approval_state",
        "openweight.backend",
        "openweight.decision",
        "openweight.dependency",
        "openweight.operation",
        "openweight.protocol",
        "openweight.request_id",
        "openweight.result",
        "openweight.source_scope",
        "openweight.tool_name",
    }
)
SAFE_BACKENDS: Final = frozenset({"gpt-oss", "muse-glimmer", "other"})
SAFE_TOOLS: Final = frozenset(
    {
        "get_employee_record",
        "get_contractor_record",
        "get_access_request",
        "search_web",
        "other",
    }
)
SAFE_MCP_TOOLS: Final = frozenset(
    {
        "search_policy",
        "lookup_employee",
        "lookup_contractor",
        "lookup_access_request",
        "list_access_requests",
        "propose_access_request_status",
        "resume_access_request_approval",
        "other",
    }
)
SAFE_DEPENDENCIES: Final = frozenset(
    {
        "configuration",
        "approval_persistence",
        "model_backend",
        "postgresql",
        "web_search",
        "mlflow",
        "other",
    }
)
SAFE_ERROR_TYPES: Final = frozenset(
    {
        "approval_conflict",
        "authentication_error",
        "authorization_error",
        "backend_unavailable",
        "database_unavailable",
        "dependency_unavailable",
        "internal_error",
        "not_found",
        "rate_limited",
        "unsafe_result",
        "validation_error",
    }
)
SAFE_OPERATIONS: Final = frozenset(
    {
        "agent",
        "approval_proposal",
        "approval_resume",
        "backend",
        "controlled_write",
        "http",
        "mcp",
        "policy_query",
        "readiness",
        "retrieval",
        "tool",
        "other",
    }
)
SAFE_RESULTS: Final = frozenset(
    {
        "approved",
        "disabled",
        "error",
        "rejected",
        "success",
        "unavailable",
        "other",
    }
)
SAFE_DECISIONS: Final = frozenset({"approve", "reject", "other"})
SAFE_METHODS: Final = frozenset(
    {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT", "OTHER"}
)

HTTP_LATENCY_BUCKETS: Final = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    120.0,
    300.0,
)
OPERATION_LATENCY_BUCKETS: Final = (
    0.001,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    120.0,
    300.0,
)


def _bounded(value: str, allowed: frozenset[str], fallback: str) -> str:
    return value if value in allowed else fallback


def safe_route(route: str | None) -> str:
    """Return one known route template, never a raw URL path."""

    return route if route in SAFE_HTTP_ROUTES else "unmatched"


def safe_method(method: str) -> str:
    return _bounded(method.upper(), SAFE_METHODS, "OTHER")


def safe_backend(backend: str) -> str:
    return _bounded(backend, SAFE_BACKENDS, "other")


def safe_tool(tool_name: str) -> str:
    return _bounded(tool_name, SAFE_TOOLS, "other")


def safe_mcp_tool(tool_name: str) -> str:
    return _bounded(tool_name, SAFE_MCP_TOOLS, "other")


def safe_dependency(dependency: str) -> str:
    return _bounded(dependency, SAFE_DEPENDENCIES, "other")


def safe_error_type(error_type: str) -> str:
    return _bounded(error_type, SAFE_ERROR_TYPES, "internal_error")


def safe_operation(operation: str) -> str:
    return _bounded(operation, SAFE_OPERATIONS, "other")


def safe_result(result: str) -> str:
    return _bounded(result, SAFE_RESULTS, "other")


def safe_decision(decision: str) -> str:
    return _bounded(decision, SAFE_DECISIONS, "other")


def safe_span_attributes(
    attributes: Mapping[str, bool | int | float | str],
) -> dict[str, bool | int | float | str]:
    """Reject any attribute not in the explicit telemetry allowlist."""

    unknown = set(attributes).difference(SAFE_SPAN_ATTRIBUTES)
    if unknown:
        raise ValueError(
            "Unsafe trace attribute(s): " + ", ".join(sorted(unknown))
        )
    return dict(attributes)


@dataclass(frozen=True)
class TraceContext:
    trace_id: str | None
    span_id: str | None


class Observability:
    """Application-owned, isolated metrics registry and optional tracer."""

    def __init__(
        self,
        *,
        service_name: str,
        environment: str,
        metrics_enabled: bool,
        tracing_enabled: bool,
        span_exporter: SpanExporter | None = None,
        synchronous_tracing: bool = False,
    ) -> None:
        self.metrics_enabled = metrics_enabled
        self.tracing_enabled = tracing_enabled
        self.registry = CollectorRegistry(auto_describe=True)
        self._provider: TracerProvider | None = None

        if tracing_enabled:
            provider = TracerProvider(
                resource=Resource.create(
                    {
                        "service.name": service_name,
                        "deployment.environment.name": environment,
                    }
                )
            )
            if span_exporter is not None:
                processor = (
                    SimpleSpanProcessor(span_exporter)
                    if synchronous_tracing
                    else BatchSpanProcessor(span_exporter)
                )
                provider.add_span_processor(processor)
            self._provider = provider
            self.tracer: Tracer = provider.get_tracer(
                "openweight_platform.api",
            )
        else:
            self.tracer = trace.NoOpTracerProvider().get_tracer(
                "openweight_platform.api"
            )

        self.http_requests = Counter(
            "openweight_http_requests_total",
            "Completed HTTP requests.",
            ("method", "route", "status_class"),
            registry=self.registry,
        )
        self.http_latency = Histogram(
            "openweight_http_request_duration_seconds",
            "HTTP request latency in seconds.",
            ("method", "route"),
            buckets=HTTP_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.http_in_flight = Gauge(
            "openweight_http_requests_in_flight",
            "HTTP requests currently in flight.",
            ("method",),
            registry=self.registry,
        )
        self.errors = Counter(
            "openweight_errors_total",
            "Sanitized application errors.",
            ("operation", "error_type"),
            registry=self.registry,
        )
        self.agent_requests = Counter(
            "openweight_agent_requests_total",
            "Agent executions by backend and bounded result.",
            ("backend", "result"),
            registry=self.registry,
        )
        self.agent_latency = Histogram(
            "openweight_agent_duration_seconds",
            "Agent execution latency in seconds.",
            ("backend",),
            buckets=OPERATION_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.backend_calls = Counter(
            "openweight_backend_calls_total",
            "Model backend calls by backend and bounded result.",
            ("backend", "result"),
            registry=self.registry,
        )
        self.backend_latency = Histogram(
            "openweight_backend_call_duration_seconds",
            "Model backend call latency in seconds.",
            ("backend",),
            buckets=OPERATION_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.retrieval_operations = Counter(
            "openweight_retrieval_operations_total",
            "Policy retrieval operations by bounded result.",
            ("retriever", "result"),
            registry=self.registry,
        )
        self.retrieval_latency = Histogram(
            "openweight_retrieval_duration_seconds",
            "Policy retrieval latency in seconds.",
            ("retriever",),
            buckets=OPERATION_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.tool_calls = Counter(
            "openweight_tool_calls_total",
            "Tool calls by fixed tool name and bounded result.",
            ("tool_name", "result"),
            registry=self.registry,
        )
        self.tool_latency = Histogram(
            "openweight_tool_duration_seconds",
            "Tool execution latency in seconds.",
            ("tool_name",),
            buckets=OPERATION_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.approval_proposals = Counter(
            "openweight_approval_proposals_total",
            "Approval proposals by bounded result.",
            ("result",),
            registry=self.registry,
        )
        self.approval_latency = Histogram(
            "openweight_approval_duration_seconds",
            "Approval proposal/resume latency in seconds.",
            ("operation",),
            buckets=OPERATION_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.approval_resumes = Counter(
            "openweight_approval_resumes_total",
            "Approval resumes by requested decision and bounded result.",
            ("decision", "result"),
            registry=self.registry,
        )
        self.controlled_writes = Counter(
            "openweight_controlled_writes_total",
            "Approval-path controlled write attempts and results.",
            ("action_type", "result"),
            registry=self.registry,
        )
        self.controlled_write_latency = Histogram(
            "openweight_controlled_write_duration_seconds",
            "Controlled action executor latency in seconds.",
            ("action_type",),
            buckets=OPERATION_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.dependency_ready = Gauge(
            "openweight_dependency_ready",
            "Dependency readiness: 1 ready, 0 unavailable, -1 disabled.",
            ("dependency",),
            registry=self.registry,
        )
        self.mcp_tool_calls = Counter(
            "openweight_mcp_tool_calls_total",
            "MCP tool calls by fixed catalog name and bounded result.",
            ("tool_name", "result"),
            registry=self.registry,
        )
        self.mcp_tool_latency = Histogram(
            "openweight_mcp_tool_duration_seconds",
            "MCP tool execution latency in seconds.",
            ("tool_name",),
            buckets=OPERATION_LATENCY_BUCKETS,
            registry=self.registry,
        )

    @contextmanager
    def span(
        self,
        name: str,
        attributes: Mapping[str, bool | int | float | str] | None = None,
        *,
        error_type: str = "internal_error",
    ) -> Iterator[Span]:
        checked = safe_span_attributes(attributes or {})
        with self.tracer.start_as_current_span(
            name,
            attributes=checked,
            record_exception=False,
            set_status_on_exception=False,
        ) as current:
            try:
                yield current
            except BaseException:
                current.set_attribute("error.type", safe_error_type(error_type))
                current.set_status(Status(StatusCode.ERROR))
                raise

    @staticmethod
    def set_span_attributes(
        span: Span,
        attributes: Mapping[str, bool | int | float | str],
    ) -> None:
        for key, value in safe_span_attributes(attributes).items():
            span.set_attribute(key, value)

    @staticmethod
    def mark_span_error(span: Span, error_type: str) -> None:
        span.set_attribute("error.type", safe_error_type(error_type))
        span.set_status(Status(StatusCode.ERROR))

    @staticmethod
    def trace_context() -> TraceContext:
        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return TraceContext(trace_id=None, span_id=None)
        return TraceContext(
            trace_id=f"{context.trace_id:032x}",
            span_id=f"{context.span_id:016x}",
        )

    def metrics_payload(self) -> bytes:
        return generate_latest(self.registry) if self.metrics_enabled else b""

    @property
    def metrics_content_type(self) -> str:
        return CONTENT_TYPE_LATEST

    def observe_http_start(self, method: str) -> None:
        if self.metrics_enabled:
            self.http_in_flight.labels(method=safe_method(method)).inc()

    def observe_http_end(
        self,
        *,
        method: str,
        route: str | None,
        status_code: int,
        seconds: float,
    ) -> None:
        if not self.metrics_enabled:
            return
        bounded_method = safe_method(method)
        bounded_route = safe_route(route)
        status_class = f"{status_code // 100}xx" if 100 <= status_code <= 599 else "other"
        self.http_in_flight.labels(method=bounded_method).dec()
        self.http_requests.labels(
            method=bounded_method,
            route=bounded_route,
            status_class=status_class,
        ).inc()
        self.http_latency.labels(
            method=bounded_method,
            route=bounded_route,
        ).observe(seconds)

    def record_error(self, *, operation: str, error_type: str) -> None:
        if self.metrics_enabled:
            self.errors.labels(
                operation=safe_operation(operation),
                error_type=safe_error_type(error_type),
            ).inc()

    def record_agent(self, *, backend: str, result: str, seconds: float) -> None:
        if self.metrics_enabled:
            bounded_backend = safe_backend(backend)
            self.agent_requests.labels(
                backend=bounded_backend,
                result=safe_result(result),
            ).inc()
            self.agent_latency.labels(backend=bounded_backend).observe(seconds)

    def record_backend(self, *, backend: str, result: str, seconds: float) -> None:
        if self.metrics_enabled:
            bounded_backend = safe_backend(backend)
            self.backend_calls.labels(
                backend=bounded_backend,
                result=safe_result(result),
            ).inc()
            self.backend_latency.labels(backend=bounded_backend).observe(seconds)

    def record_retrieval(self, *, result: str, seconds: float) -> None:
        if self.metrics_enabled:
            self.retrieval_operations.labels(
                retriever="policy",
                result=safe_result(result),
            ).inc()
            self.retrieval_latency.labels(retriever="policy").observe(seconds)

    def record_tool(self, *, tool_name: str, result: str, seconds: float) -> None:
        if self.metrics_enabled:
            bounded_tool = safe_tool(tool_name)
            self.tool_calls.labels(
                tool_name=bounded_tool,
                result=safe_result(result),
            ).inc()
            self.tool_latency.labels(tool_name=bounded_tool).observe(seconds)

    def record_mcp(self, *, tool_name: str, result: str, seconds: float) -> None:
        if self.metrics_enabled:
            bounded_tool = safe_mcp_tool(tool_name)
            self.mcp_tool_calls.labels(
                tool_name=bounded_tool,
                result=safe_result(result),
            ).inc()
            self.mcp_tool_latency.labels(tool_name=bounded_tool).observe(seconds)

    def record_approval_proposal(self, *, result: str, seconds: float) -> None:
        if self.metrics_enabled:
            self.approval_proposals.labels(result=safe_result(result)).inc()
            self.approval_latency.labels(operation="proposal").observe(seconds)

    def record_approval_resume(
        self,
        *,
        decision: str,
        result: str,
        seconds: float,
    ) -> None:
        if self.metrics_enabled:
            self.approval_resumes.labels(
                decision=safe_decision(decision),
                result=safe_result(result),
            ).inc()
            self.approval_latency.labels(operation="resume").observe(seconds)

    def record_controlled_write(self, *, result: str, seconds: float) -> None:
        if self.metrics_enabled:
            self.controlled_writes.labels(
                action_type="set_access_request_status",
                result=safe_result(result),
            ).inc()
            self.controlled_write_latency.labels(
                action_type="set_access_request_status"
            ).observe(seconds)

    def record_dependency(self, *, dependency: str, status: str) -> None:
        if not self.metrics_enabled:
            return
        value = 1 if status == "ready" else -1 if status == "disabled" else 0
        self.dependency_ready.labels(
            dependency=safe_dependency(dependency)
        ).set(value)

    def shutdown(self) -> None:
        if self._provider is not None:
            self._provider.shutdown()


def create_observability(
    settings: ServiceSettings,
    *,
    span_exporter: SpanExporter | None = None,
    synchronous_tracing: bool = False,
) -> Observability:
    """Build local metrics and optional OTLP tracing from validated settings."""

    exporter = span_exporter
    if settings.tracing_enabled and exporter is None:
        if settings.otlp_endpoint is None:
            raise ValueError("Tracing requires a configured OTLP endpoint")
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        exporter = OTLPSpanExporter(endpoint=settings.otlp_endpoint)
    return Observability(
        service_name=settings.observability_service_name,
        environment=settings.environment,
        metrics_enabled=settings.metrics_enabled,
        tracing_enabled=settings.tracing_enabled,
        span_exporter=exporter,
        synchronous_tracing=synchronous_tracing,
    )


def elapsed_seconds(started: float) -> float:
    return max(0.0, time.perf_counter() - started)


__all__ = [
    "Observability",
    "SAFE_ERROR_TYPES",
    "SAFE_HTTP_ROUTES",
    "SAFE_MCP_TOOLS",
    "SAFE_SPAN_ATTRIBUTES",
    "TraceContext",
    "create_observability",
    "elapsed_seconds",
    "safe_backend",
    "safe_error_type",
    "safe_mcp_tool",
    "safe_route",
    "safe_span_attributes",
    "safe_tool",
]
