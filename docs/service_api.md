# OpenWeight service API

The production-style HTTP boundary is `openweight_platform.api.app:app`. It
adapts the existing model-routed LangGraph, policy retrieval, read-only
operations tools, optional web search, and checkpointed human-approval graph;
it does not create a second agent implementation.

## Local startup

Install `requirements.txt`, configure the environment (see `.env.example`),
then bind locally by default:

```bash
PYTHONPATH=src .venv/bin/python -m openweight_platform.api.run
```

`OPENWEIGHT_API_HOST` and `OPENWEIGHT_API_PORT` control the bind address; their
defaults are `127.0.0.1` and `8000`.

An optional official MCP Streamable HTTP surface can share this process at
`/mcp`; it is an interoperability adapter over the same secured runtime, not a
replacement product API. See [mcp.md](mcp.md).

The service imports and starts without loading a model. The configured backend
is built and loaded on the first agent or grounded-policy query. FastAPI
lifespan cleanup closes retrieval resources and unloads an initialized backend.

## Public routes

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/healthz` | Process liveness only; performs no dependency probes. |
| `GET` | `/readyz` | Reports sanitized configuration, backend, PostgreSQL, optional web, and optional MLflow states. Returns 503 when a required dependency is unavailable. |
| `GET` | `/v1/service-info` | Safe version, environment, build, configured backend alias, and process-local inference initialization state. |
| `POST` | `/v1/agent/query` | Runs one existing model-routed query and returns a typed, reasoning-free result. |
| `POST` | `/v1/policy/query` | Retrieves internal policy evidence, generates a grounded answer, validates citations, and returns a typed employee-facing result. |
| `POST` | `/v1/policy/query/stream` | Streams employee-facing grounded-answer text, then emits the same authoritative typed policy result. |
| `GET` | `/v1/access-requests` | Lists bounded fictional access-request records; optional status filter and a maximum limit of 100. |
| `GET` | `/v1/access-requests/{id}` | Returns one typed fictional access-request record. |
| `POST` | `/v1/actions/access-requests/{id}/proposals` | Validates and pauses one narrow status-change proposal for approval. Performs no write. |
| `POST` | `/v1/approvals/{approval_id}/resume` | Explicitly approves or rejects one pending proposal. Only approval can reach the controlled executor. |

OpenAPI is available through FastAPI's standard `/openapi.json` and `/docs`
routes. Training, evaluation, arbitrary tool execution, arbitrary SQL, and
generic write endpoints are intentionally absent.

The optional compiled React application uses only explicit routes (`/`,
`/overview`, `/assistant`, `/access-requests`, `/approvals`, and `/system`).
There is deliberately no catch-all server route, so static delivery cannot
swallow `/mcp`, `/v1/*`, probes, metrics, OpenAPI, or documentation. See
[frontend.md](frontend.md).

`GET /metrics` is an operational Prometheus endpoint and is intentionally
hidden from the product OpenAPI schema. Its access mode is configurable as
`public`, `protected`, or `disabled`; the Azure contract uses `disabled`.
See `docs/observability.md`.

## Contracts and errors

Request and response bodies use strict Pydantic models. Agent completions can
return safe structured results and citations/evidence intended for user
exposure. The response boundary rejects hidden-reasoning and secret-bearing
key classes such as `reasoning_content`, `scratchpad`, `authorization`, and
`password`.

`POST /v1/policy/query` is the focused product contract used by Policy &
Evidence. It reuses the same `generate_grounded_answer` retrieval, prompt,
citation extraction, and citation-validation path as `scripts/ask_policy_rag.py`.
Its source scope is explicitly `internal_policy`; it does not route to Tavily.
The response includes only the answer, used citation IDs, validation status,
and safe policy evidence fields (policy ID, title, domain, chunk ID, and
content). It omits prompts, model traces, token/performance data, database
internals, and source filesystem paths. A retrieval with no evidence returns a
typed `insufficient_evidence` result without invoking the model; the shared
generator also maps its exact evidence-insufficiency outcome to that contract.
Any other generated answer that omits citations or cites outside its supplied
evidence fails closed.

The shared generation prompt requests a short direct answer, preserves each
source's policy/domain boundary, and keeps universal requirements separate
from additional conditional rules (such as duration limits). This reduces
scope-compression and overgeneralization errors. It also requires factual
conclusions to be directly supported and citations to remain adjacent instead
of becoming a detached bibliography. These instructions are not a semantic
verifier. Citation validation checks that cited IDs belong to retrieved
evidence, not that each claim faithfully represents that evidence. Employees
must still review the cited passages, particularly approvals and exceptions.

The streaming policy route uses server-sent events over the same authorized
POST contract. It emits bounded `searching` and `generating` stage events,
followed by `answer_delta` events containing only GPT-OSS's final answer
channel. Analysis/reasoning channels, prompts, retrieval internals, and model
traces are never emitted. Citation syntax in partial text is provisional: the
terminal `complete` event contains the authoritative typed response after
citation extraction and validation, and only then should clients finalize
citation controls and evidence cards. Once HTTP streaming has begun, failures
are delivered as a sanitized `error` event rather than raw exceptions.

The API presentation boundary removes model `SOURCE` wrappers from both
stream deltas and the final answer. The shared RAG generator validates the
original answer before this display cleanup, so structured citation IDs and
validation status are preserved. Partial wrappers are buffered server-side
and never need to be scraped from text by React.

Errors use this stable shape:

```json
{
  "request_id": "...",
  "status": "error",
  "error": {"code": "state_conflict", "message": "..."}
}
```

The API distinguishes invalid operations (400), missing authentication (401),
insufficient permission (403), absent resources (404), state conflicts (409),
Pydantic schema failures (422), unavailable dependencies (503), and unexpected
failures (500). Public errors omit exception messages, stack traces, prompts,
credentials, tokens, and environment dumps.

## Request identity and logs

A syntactically valid `X-Request-ID` supplied by a client is retained;
otherwise the API creates one. It is returned in the same header and in typed
responses, passed into agent graph metadata, and included in JSON access logs.
It is correlation metadata, not authentication or authorization. Independently
validated bearer-token claims provide identity when authentication is enabled.

Logs contain only an allowlist of operational fields: timestamp, level, event,
request ID, route template, method, status, latency, backend alias, operation,
fixed tool/action type, approval state, sanitized error classification, and
optional trace/span IDs. Raw prompts, model output, reasoning, authorization
headers, credentials, dynamic route identifiers, and full environment data
are not logged.

## Configuration

Configuration is environment-driven. The `OPENWEIGHT_*` variables cover the
environment/build identity, log level, bind defaults, backend selection,
generation limit, PostgreSQL readiness policy, web-search enablement, and
backend-specific connection values. Existing `POSTGRES_*`, `TAVILY_API_KEY`,
and optional `MLFLOW_TRACKING_URI` variables remain integration inputs. No
secret is hard-coded or returned by service metadata/readiness.

`OPENWEIGHT_MAX_NEW_TOKENS` defaults to 768 for the service path. This is a
configurable upper bound selected to avoid truncating normal cited policy
answers; the backend may stop earlier. The backend factory and offline
benchmark contracts retain their own task-specific defaults.

PostgreSQL is required by default because policy retrieval, operations lookup,
and approval-gated writes use it. It can be marked optional for a constrained
mode with `OPENWEIGHT_DATABASE_REQUIRED=false`; database-backed requests still
fail safely with 503. Tavily and runtime MLflow configuration are optional.

`OPENWEIGHT_BUILD_SHA` is injected by a future build pipeline. The service
does not require a Git checkout or expose repository paths/history at runtime.

## Approval and write safety

The LLM is not the security boundary. A model result cannot directly authorize
a consequential write. The only HTTP write workflow is:

```text
agent/application proposal
-> deterministic action and argument validation
-> LangGraph interrupt/checkpoint
-> explicit human approval
-> fixed parameterized executor
-> structured result
```

The proposal is based on the current access-request record and permits only
`set_access_request_status` with an allowlisted status. No arbitrary table,
column, SQL, or tool name is accepted. Rejection never calls the executor.

Local/test mode retains in-memory checkpoints. PostgreSQL mode uses LangGraph's
PostgreSQL saver, a row-locked approval-session record, and a unique
transactional effect ledger. The claim, fixed status update, and stored result
commit together, so restart or concurrent resume cannot repeat the one
controlled database effect. See `docs/approval_durability.md`.

Product routes can require RS256 OIDC/JWT authentication with bounded query,
proposal, approval, and metrics permissions. See `docs/security.md`.

## Deployment-specific boundaries

The core service does not add a reverse proxy/WAF, a Prometheus/Grafana
deployment, or live enterprise tenant federation. The canonical local command
binds to loopback. The deployed public Hugging Face profile adds bounded
controls only around metered inference and uses synthetic process-local state
with authentication disabled; it is not the production security or durability
posture. Azure remains a Terraform reference architecture and is not deployed.
