# Application observability

The service emits three complementary, privacy-safe signals: structured JSON
logs, bounded Prometheus metrics, and optional OpenTelemetry traces. Local use
requires no collector or cloud credentials. Observability never changes the
approval boundary and is not a model evaluation or MLflow interface.

## Signals and correlation

Structured logs continue to go to standard output and are visible with:

```bash
docker compose logs --follow api
```

Every HTTP request has an `X-Request-ID`. A valid caller-supplied value is
retained; otherwise the API generates one. The request ID appears in safe
response metadata, logs, LangGraph invocation metadata, and trace attributes.
It is not authentication. OpenTelemetry trace and span IDs are separate and,
when tracing is enabled, appear alongside the request ID in request-completion
logs.

Log metadata is allowlisted. Supported fields include route template, method,
status, latency, backend, operation, fixed tool/action type, approval state,
dependency, sanitized error class, request ID, trace ID, and span ID. Raw URL
identifiers are replaced by route templates.

## Prometheus metrics

Metrics are enabled by default at `GET /metrics`. The route is deliberately
excluded from the product OpenAPI schema. It is operational and performs no
model load, dependency mutation, or consequential write.

The optional MCP surface adds bounded
`openweight_mcp_tool_calls_total{tool_name,result}` and
`openweight_mcp_tool_duration_seconds{tool_name}` signals. Tool names come
from the fixed catalog; request IDs, approval IDs, arguments, and result
content are never labels.

The application uses its own Prometheus registry and these bounded metrics:

| Metric | Labels |
| --- | --- |
| `openweight_http_requests_total` | `method`, `route`, `status_class` |
| `openweight_http_request_duration_seconds` | `method`, `route` |
| `openweight_http_requests_in_flight` | `method` |
| `openweight_errors_total` | `operation`, `error_type` |
| `openweight_agent_requests_total` | `backend`, `result` |
| `openweight_agent_duration_seconds` | `backend` |
| `openweight_backend_calls_total` | `backend`, `result` |
| `openweight_backend_call_duration_seconds` | `backend` |
| `openweight_retrieval_operations_total` | `retriever`, `result` |
| `openweight_retrieval_duration_seconds` | `retriever` |
| `openweight_tool_calls_total` | `tool_name`, `result` |
| `openweight_tool_duration_seconds` | `tool_name` |
| `openweight_mcp_tool_calls_total` | `tool_name`, `result` |
| `openweight_mcp_tool_duration_seconds` | `tool_name` |
| `openweight_approval_proposals_total` | `result` |
| `openweight_approval_resumes_total` | `decision`, `result` |
| `openweight_approval_duration_seconds` | `operation` |
| `openweight_controlled_writes_total` | `action_type`, `result` |
| `openweight_controlled_write_duration_seconds` | `action_type` |
| `openweight_dependency_ready` | `dependency` |

Route labels use FastAPI route templates, never raw paths. Tool names,
backends, dependencies, decisions, results, operations, and error types are
mapped through finite allowlists. Request IDs, approval IDs, access-request
IDs, prompts, responses, document content, exception messages, and SQL never
become metric labels.

Local checks:

```bash
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/metrics
```

Set `OPENWEIGHT_METRICS_ENABLED=false` to omit the endpoint. Health and
readiness stay lightweight and do not depend on a metrics scrape.

## OpenTelemetry tracing

Tracing uses the standard OpenTelemetry API/SDK and is disabled by default.
When enabled, the service creates coarse operation spans for:

- HTTP requests
- agent execution
- backend load/generation/unload
- policy retrieval
- fixed operations and web tools
- approval proposal/resume
- the fixed controlled action executor
- dependency readiness

There is no token-level tracing. Exceptions are represented only by a fixed
error classification; exception messages, events, and stack traces are not
exported by the application tracer.

To send OTLP/HTTP traces to a collector, supply a complete traces endpoint:

```bash
OPENWEIGHT_TRACING_ENABLED=true
OPENWEIGHT_OTLP_ENDPOINT=https://collector.example/v1/traces
```

The endpoint must be HTTP(S) and must not contain embedded credentials.
Exporter credentials, if later required, belong in the hosting platform's
secret configuration rather than committed files. No collector is required
when tracing is disabled.

Azure Monitor/Application Insights can later be connected at this boundary
through a supported Azure Monitor OpenTelemetry distribution or an OTLP
collector. Business logic and metric definitions do not contain Azure-specific
code.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `OPENWEIGHT_OBSERVABILITY_ENABLED` | `true` | Master switch for metrics/traces; JSON application logs remain available. |
| `OPENWEIGHT_METRICS_ENABLED` | `true` | Expose the local Prometheus endpoint. |
| `OPENWEIGHT_METRICS_ACCESS_MODE` | `public` | Select `public`, bearer-token `protected`, or `disabled`; cloud uses disabled. |
| `OPENWEIGHT_TRACING_ENABLED` | `false` | Build/export OpenTelemetry spans. |
| `OPENWEIGHT_OBSERVABILITY_SERVICE_NAME` | `openweight-platform` | Low-cardinality telemetry service identity. |
| `OPENWEIGHT_OTLP_ENDPOINT` | empty | Optional OTLP/HTTP traces endpoint. |

The Compose service passes these values from the environment. It does not add
Prometheus, Grafana, or an OpenTelemetry collector container.

## Privacy boundary and limitations

Only explicit attributes are accepted. Prompts, model responses, reasoning,
retrieved documents, policy text, operational records, names, email addresses,
SQL, database URLs, authorization data, API keys, and environment dumps are
not telemetry attributes. Product error responses remain sanitized.

Metrics are process-local. A restart resets counters, and multiple replicas
need an external scraper/collector for aggregation. Traces are sampled/exported
according to the later collector or hosting configuration; D10 does not add a
cloud exporter account or Azure resource. Bearer tokens and claims are never
telemetry attributes. Durable approval checkpoints are a separate PostgreSQL
runtime concern and do not change these privacy rules.

Rate limiting, frontend, Hugging Face hosting, and live Azure deployment remain
separate stages.
