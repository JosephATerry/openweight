import {
  Activity,
  ArrowDown,
  Bot,
  Boxes,
  CheckCircle2,
  Cloud,
  Code2,
  Container,
  Database,
  ExternalLink,
  GitBranch,
  KeyRound,
  Network,
  Radio,
  ServerCog,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { HealthResponse, ReadinessResponse, ServiceInfoResponse } from "../api/types";
import { ErrorState, LoadingState } from "../components/StatePanel";
import { StatusBadge } from "../components/StatusBadge";

const MCP_TOOLS = [
  "search_policy",
  "lookup_employee",
  "lookup_contractor",
  "lookup_access_request",
  "list_access_requests",
  "propose_access_request_status",
  "resume_access_request_approval",
];

interface RuntimeState {
  health: HealthResponse;
  readiness: ReadinessResponse;
  service: ServiceInfoResponse;
}

function configuredBackendLabel(backend: string): string {
  if (backend === "muse-glimmer") return "Muse Glimmer";
  if (backend === "gpt-oss") return "GPT-OSS";
  return backend;
}

function configuredModelLabel(service: ServiceInfoResponse): string {
  if (service.model_id === "openai/gpt-oss-20b") return "GPT-OSS 20B";
  return configuredBackendLabel(service.backend);
}

function inferenceStateLabel(state: ServiceInfoResponse["inference_state"]): string {
  const labels: Record<ServiceInfoResponse["inference_state"], string> = {
    not_initialized: "Not active",
    loaded: "Loaded",
    unconfigured: "Unconfigured",
    not_used: "Not yet used",
    requesting: "Requesting",
    available: "Available",
    unavailable: "Unavailable",
  };
  return labels[state];
}

function inferenceStateDetail(service: ServiceInfoResponse): string {
  if (service.deployment_profile === "huggingface") {
    if (!service.inference_configured) return "HF_TOKEN is not configured";
    if (service.inference_state === "not_used") return "No provider request has been made";
    if (service.inference_state === "requesting") return "A bounded provider request is active";
    if (service.inference_state === "available") return "Last provider request succeeded";
    return "Last provider request was unavailable";
  }
  return service.inference_state === "loaded"
    ? "Initialized in this API process"
    : "Lazy initialization on first AI request";
}

function inferenceProviderLabel(service: ServiceInfoResponse): string {
  if (service.inference_provider.startsWith("huggingface:")) {
    return `Hugging Face Inference Providers · ${service.inference_provider.split(":")[1]}`;
  }
  return "Local model runtime";
}

export function SystemPage() {
  const [runtime, setRuntime] = useState<RuntimeState | null>(null);
  const [runtimeError, setRuntimeError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      api.health(controller.signal),
      api.readiness(controller.signal),
      api.serviceInfo(controller.signal),
    ])
      .then(([health, readiness, service]) => setRuntime({ health, readiness, service }))
      .catch(() => {
        if (!controller.signal.aborted) setRuntimeError(true);
      });
    return () => controller.abort();
  }, []);

  return (
    <div className="page">
      <div className="page-heading page-heading--split">
        <div>
          <p className="eyebrow">System architecture</p>
          <h1>One service core.<br />Two governed surfaces.</h1>
          <p>React serves the product experience. MCP provides interoperability. Both reuse the same validation, authorization, durable approval, and execution boundaries.</p>
        </div>
        <div className="architecture-state"><span><Cloud aria-hidden="true" /></span><div><strong>Azure reference ready</strong><small>Architecture and Terraform exist. No cloud resources are currently deployed.</small></div></div>
      </div>

      <section aria-labelledby="services-heading">
        <div className="section-heading">
          <div><p className="eyebrow">Application services</p><h2 id="services-heading">Runtime posture</h2></div>
          {runtime ? (
            <StatusBadge
              label={runtime.readiness.status === "ready" ? "Ready to serve" : "Attention required"}
              tone={runtime.readiness.status === "ready" ? "ready" : "unavailable"}
            />
          ) : null}
        </div>
        {!runtime && !runtimeError ? <LoadingState label="Checking service availability…" /> : null}
        {runtimeError ? <ErrorState title="Runtime status is unavailable" message="The architecture reference remains available below." /> : null}
        {runtime ? (
          <div className="metric-grid">
            <article className="metric-card">
              <span className="metric-card__icon"><ServerCog aria-hidden="true" /></span>
              <span className="metric-card__label">API service</span>
              <strong>{runtime.health.status === "alive" ? "Alive" : "Unavailable"}</strong>
              <small>{runtime.service.service} · v{runtime.service.version}</small>
            </article>
            <article className="metric-card">
              <span className="metric-card__icon"><Activity aria-hidden="true" /></span>
              <span className="metric-card__label">Dependencies</span>
              <strong>{runtime.readiness.dependencies.filter((item) => item.status === "ready").length}/{runtime.readiness.dependencies.length} ready</strong>
              <small>Sanitized readiness checks; no consequential probes</small>
            </article>
            <article className="metric-card">
              <span className="metric-card__icon"><Bot aria-hidden="true" /></span>
              <span className="metric-card__label">AI inference</span>
              <strong>{inferenceStateLabel(runtime.service.inference_state)}</strong>
              <small className="metric-card__details">
                <span>Configured model: {configuredModelLabel(runtime.service)}</span>
                <span>{inferenceProviderLabel(runtime.service)}</span>
                <span>{inferenceStateDetail(runtime.service)}</span>
              </small>
            </article>
            <article className="metric-card">
              <span className="metric-card__icon"><Radio aria-hidden="true" /></span>
              <span className="metric-card__label">Environment</span>
              <strong>{runtime.service.environment}</strong>
              <small>{runtime.service.build_sha ? `Build ${runtime.service.build_sha.slice(0, 8)}` : "Build metadata injected at release"}</small>
            </article>
          </div>
        ) : null}
      </section>

      <section className="architecture-board" aria-labelledby="runtime-heading">
        <div className="section-heading"><div><p className="eyebrow">Runtime topology</p><h2 id="runtime-heading">Shared control plane</h2></div><span className="architecture-board__caption">Product paths stay explicit</span></div>
        <div className="architecture-flow">
          <div className="architecture-column">
            <article className="architecture-node architecture-node--surface"><Code2 aria-hidden="true" /><div><strong>React product UI</strong><small>Human workflows over typed FastAPI contracts</small></div></article>
            <article className="architecture-node architecture-node--surface"><Network aria-hidden="true" /><div><strong>MCP clients</strong><small>Bounded interoperability over Streamable HTTP</small></div></article>
          </div>
          <div className="architecture-connector"><span>HTTPS</span><ArrowDown aria-hidden="true" /></div>
          <article className="architecture-node architecture-node--core"><Container aria-hidden="true" /><div><strong>FastAPI service boundary</strong><small>Authentication · authorization · validation · observability</small></div></article>
          <div className="architecture-connector"><span>Guarded service calls</span><ArrowDown aria-hidden="true" /></div>
          <div className="architecture-column architecture-column--services">
            <article className="architecture-node"><GitBranch aria-hidden="true" /><div><strong>LangGraph orchestration</strong><small>{runtime?.service.demo_state === "ephemeral" ? "Sandbox approvals reset when the demo restarts" : "Routing and durable approval checkpoints"}</small></div></article>
            <article className="architecture-node"><Database aria-hidden="true" /><div><strong>{runtime?.service.demo_state === "ephemeral" ? "Portable policy index" : "PostgreSQL + pgvector"}</strong><small>{runtime?.service.demo_state === "ephemeral" ? "Verified synthetic evidence; ephemeral demo state" : "Operational data, policy evidence, idempotency ledger"}</small></div></article>
            <article className="architecture-node"><ShieldCheck aria-hidden="true" /><div><strong>Controlled executor</strong><small>Fixed parameterized effects only</small></div></article>
          </div>
        </div>
      </section>

      <div className="system-grid">
        <section className="mcp-panel" aria-labelledby="mcp-heading">
          <div className="mcp-panel__header">
            <span className="mcp-panel__glyph"><Boxes aria-hidden="true" /></span>
            <div><p className="eyebrow">Interoperability</p><h2 id="mcp-heading">MCP 2026-07-28</h2></div>
            <span className="protocol-pill">/mcp</span>
          </div>
          <p>Official Streamable HTTP transport over the same secured application runtime. Client-advertised capabilities never become authorization.</p>
          <div className="mcp-tool-list">
            {MCP_TOOLS.map((tool, index) => <div key={tool}><span>{String(index + 1).padStart(2, "0")}</span><code>{tool}</code></div>)}
          </div>
          <div className="mcp-panel__footer"><KeyRound aria-hidden="true" /><span>Same OIDC roles, approval gate, and exactly-once ledger</span></div>
        </section>

        <section className="boundary-panel" aria-labelledby="boundary-heading">
          <p className="eyebrow">Security invariant</p>
          <h2 id="boundary-heading">The model is not the security boundary.</h2>
          <div className="boundary-sequence">
            {["Model or agent proposal", "Deterministic validation", "Explicit human approval", "Controlled execution", "Auditable result"].map((step, index) => (
              <div key={step}><span>{index + 1}</span><strong>{step}</strong>{index < 4 ? <ArrowDown aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}</div>
            ))}
          </div>
          <p className="boundary-panel__note"><ExternalLink aria-hidden="true" />No generic SQL, shell, arbitrary tool, or arbitrary write surface exists.</p>
        </section>
      </div>

      <section className="system-detail-grid" aria-label="Technical platform details">
        <article>
          <p className="eyebrow">Governed actions</p>
          <h2>{runtime?.service.demo_state === "ephemeral" ? "Sandboxed human control" : "Durable human control"}</h2>
          <p>{runtime?.service.demo_state === "ephemeral" ? "This public profile demonstrates proposal, approval, and fixed execution against synthetic process-local state. It resets on restart; the full architecture uses PostgreSQL durability." : "LangGraph checkpoints preserve pending approvals in PostgreSQL. A transactional execution ledger prevents the same approved effect from running twice."}</p>
        </article>
        <article>
          <p className="eyebrow">Observability</p>
          <h2>Safe operational signals</h2>
          <p>OpenTelemetry traces, structured logs, and bounded metrics correlate requests without exporting prompts, tokens, reasoning, or document contents.</p>
        </article>
        <article>
          <p className="eyebrow">Deployment architecture</p>
          <h2>Portable container boundary</h2>
          <p>The current Docker application remains cloud-portable. Azure architecture and Terraform are documented, but no Azure resources are deployed.</p>
        </article>
      </section>
    </div>
  );
}
