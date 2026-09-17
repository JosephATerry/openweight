# Model Context Protocol interface

## Purpose and boundary

The Model Context Protocol (MCP) interface is an interoperability surface over
the existing OpenWeight platform. FastAPI remains the normal product API. MCP
does not implement another agent, database layer, authentication system, or
executor: its handlers authorize typed input and delegate to the same lazy
runtime, PostgreSQL services, LangGraph approval graph, and controlled action
executor used by the HTTP API.

The implementation targets the final MCP `2026-07-28` specification with the
official Python package `mcp==2.1.1`. It uses the v2 `MCPServer` and official
Streamable HTTP transport. The application exposes the endpoint at `/mcp` in
the existing FastAPI process; no additional service or container is needed.

## Protocol behavior

For `2026-07-28`, requests are stateless at the protocol layer: each request
carries the protocol revision, client identity metadata, and client
capabilities. `server/discover` reports the implemented protocol versions and
capabilities. The tool list and discovery response carry private 60-second
cache hints and are deterministic for one build.

The official SDK negotiates older supported clients through its legacy
initialize path in the same deployment. There is no separate legacy server.
New code does not use the superseded HTTP+SSE transport or build application
health around protocol ping. `/healthz` and `/readyz` remain the platform
health endpoints.

When authentication is enabled, the SDK exposes OAuth protected-resource
metadata at the standard origin-level `/.well-known/oauth-protected-resource/`
path and protects MCP Streamable HTTP with bearer authentication. The shared
RS256 JWT verifier remains authoritative for signature, issuer, audience,
subject, expiry, and not-before validation.

## Tool catalog and safety

| Tool | Classification | Permission | Behavior |
|---|---|---|---|
| `search_policy` | Read-only | `agent.query` | Searches the existing indexed policy store. |
| `lookup_employee` | Read-only | `agent.query` | Exact fictional employee lookup. |
| `lookup_contractor` | Read-only | `agent.query` | Exact fictional contractor lookup. |
| `lookup_access_request` | Read-only | `agent.query` | Exact fictional access-request lookup. |
| `list_access_requests` | Read-only | `agent.query` | Bounded, ordered list with an optional fixed status filter. |
| `propose_access_request_status` | Approval-gated, non-writing | `actions.propose` | Validates and durably records a proposal; it does not execute it. |
| `resume_access_request_approval` | Consequential, approval-gated | `approvals.resume` | Applies an explicit approve/reject decision to the already-fixed proposal. |

There is deliberately no tool for arbitrary SQL, shell commands, Python,
generic HTTP proxying, arbitrary internal tool calls, arbitrary action
arguments, training, evaluation, or model debugging.

Roles map to the same application permissions used by FastAPI:

- `OpenWeight.Reader`: read-only tools.
- `OpenWeight.Approver`: read-only tools and approval resume.
- `OpenWeight.Operator`: read-only tools, proposal creation, and approval
  resume.

Tool availability advertised by a client is not authorization. Every call is
authorized again on the server.

## Durable approval and retry behavior

The consequential path is unchanged:

```text
MCP proposal call
  -> bearer authentication and role authorization
  -> typed and deterministic business validation
  -> security.approval_sessions + LangGraph PostgreSQL checkpoint
  -> proposal returned without a write
  -> separate explicit resume call
  -> fixed controlled executor
  -> security.execution_ledger transaction
  -> exactly-once effect/result
```

MCP protocol statelessness does not discard application workflow state.
PostgreSQL `PostgresSaver`, `security.approval_sessions`, and the deterministic
`effect_id` ledger are reused directly. Sequential, concurrent, restarted, and
multi-process retries therefore use the application's durable conflict/idempotency
semantics rather than an MCP-specific in-memory retry map. The resume tool
accepts only an approval ID, decision, and optional comment; it cannot replace
the proposal's action parameters.

The Azure portfolio may scale to zero when idle but remains capped at one
replica until cloud bootstrap, failure injection, connection-capacity, and
multi-replica load testing are complete.

## Resources and prompts

No MCP Resources or Prompts are registered. The bounded tools already
provide the useful interoperability surface without creating a read-any-file
capability or publishing policy documents wholesale. Internal prompts, hidden
instructions, reasoning templates, repository files, training/evaluation
data, and model artifacts are never exposed.

## Observability and privacy

The outer FastAPI middleware supplies the application `X-Request-ID`; the MCP
tool layer reads the same context and includes it in structured results and
safe logs. OpenTelemetry trace/span IDs remain separate distributed-tracing
identifiers. Neither identifier is authentication, and neither is a metric
label.

MCP spans and metrics use only fixed fields: protocol, operation, fixed tool
name, bounded result, error classification, and latency. The metrics are:

- `openweight_mcp_tool_calls_total{tool_name,result}`
- `openweight_mcp_tool_duration_seconds{tool_name}`

Logs, metrics, spans, and safe errors never include bearer tokens, raw tool
arguments, raw operational records, policy/document text, prompts, model
responses, reasoning, database URLs, or exception messages. Client-visible
error classes are bounded: authentication/authorization, validation,
not-found, approval conflict, dependency unavailable, unsafe result, and
internal error. Unexpected exceptions are converted by the SDK to a generic
tool failure without a traceback or original exception text.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `OPENWEIGHT_MCP_ENABLED` | `false` natively; `true` in Compose | Enables the mounted MCP surface. |
| `OPENWEIGHT_MCP_PATH` | `/mcp` | Streamable HTTP path. |
| `OPENWEIGHT_MCP_SERVER_NAME` | `openweight-platform` | Safe discovery name. |
| `OPENWEIGHT_MCP_RESOURCE_SERVER_URL` | unset | Public credential-free MCP URL; required when auth and MCP are enabled. Production/demo requires HTTPS. |
| `OPENWEIGHT_MCP_ALLOWED_HOSTS` | loopback hosts | DNS-rebinding Host allowlist. |
| `OPENWEIGHT_MCP_ALLOWED_ORIGINS` | loopback origins | Browser Origin allowlist. |

Authentication follows `OPENWEIGHT_AUTH_ENABLED` and the shared issuer, audience,
and JWKS settings. There is intentionally no second MCP-auth toggle. Production
configuration fails closed when authenticated MCP lacks a public resource URL.
No credential belongs in these settings, `.env.example`, or the image.

## Local and Docker use

Native startup remains:

```bash
PYTHONPATH=src OPENWEIGHT_MCP_ENABLED=true \
  .venv/bin/python -m openweight_platform.api.run
```

The endpoint is then `http://127.0.0.1:8000/mcp`. Compose enables it in the
existing `api` container:

```bash
docker compose up -d
docker compose logs api
```

An official Python client can connect with `mcp.client.Client` using that URL.
Authenticated remote clients obtain and send an access token according to MCP
OAuth discovery; local deterministic tests use the official in-memory client
or an injected verifier and do not contact Entra.

Policy search initializes its existing embedding dependency only when that
specific tool is called. Protocol discovery and operational lookup tests do
not load Muse, GPT-OSS, Torch model weights, or a model server.

## Deliberate protocol deferrals

- **Multi Round-Trip Requests (MRTR):** not used. The existing explicit,
  durable proposal/resume contract is clearer for human approval and remains
  authoritative. No correctness depends on a live protocol round trip.
- **Tasks extension:** not advertised or implemented. The installed stable
  SDK does not provide a stable Tasks package, and application workflow
  state is not mislabeled as MCP Tasks.
- **Deprecated features:** new architecture does not use roots, sampling,
  protocol-level logging, ping-based health, legacy HTTP+SSE, or legacy
  elicitation. SDK compatibility code alone handles older protocol clients.
- **Outbound MCP client:** deferred. The current implementation provides a
  bounded server; the agent
  does not yet consume arbitrary external MCP servers.
- **MCP Apps:** deferred. OpenWeight retains its custom React product frontend.
- **Enterprise Managed Authorization:** no speculative extension is enabled.
  The existing Entra-compatible OIDC validation and bounded roles provide the
  future alignment point for managed enterprise authorization.
- **Official conformance:** project tests exercise the official client,
  modern and legacy negotiation, discovery, catalog, validation, auth, and
  calls. Any external conformance-suite result is reported separately and is
  not implied by the unit suite.

The same endpoint can use Azure Container Apps HTTPS ingress when MCP is
enabled. Deployment-specific URL and Host allowlists must be supplied. The
public React frontend and alternate Hugging Face demo remain separate and
cannot bypass the approval/security boundary.
