import { vi } from "vitest";

import type { AccessRequestRecord, PolicyQueryResponse, ServiceInfoResponse } from "../api/types";

export const FICTIONAL_ACCESS_REQUEST: AccessRequestRecord = {
  request_id: "fic-req-002",
  subject_type: "employee",
  subject_id: "fic-emp-002",
  system_name: "Fictional Finance",
  requested_role: "analyst",
  approval_status: "pending",
  requested_start_date: "2026-01-15",
  requested_end_date: "2026-06-30",
};

export interface MockApiOptions {
  listStatus?: number;
  queryStatus?: number;
  queryResult?: Partial<PolicyQueryResponse> & Record<string, unknown>;
  queryGate?: Promise<void>;
  queryCompletionGate?: Promise<void>;
  queryDeltas?: string[];
  queryStreamError?: boolean;
  queryStreamHiddenText?: string;
  resumeStatus?: number;
  serviceInfo?: Partial<ServiceInfoResponse>;
}

function policyResult(
  overrides: MockApiOptions["queryResult"],
): PolicyQueryResponse & Record<string, unknown> {
  return {
    request_id: "req-query",
    status: "answered",
    source_scope: "internal_policy",
    answer: "Privileged access requires explicit approval and evidence [EPG-ACCESS-001#1].",
    citations: ["[EPG-ACCESS-001#1]"],
    citation_valid: true,
    evidence: [{
      citation_id: "[EPG-ACCESS-001#1]",
      policy_id: "EPG-ACCESS-001",
      title: "Fictional Access Standard",
      domain: "privileged-access",
      chunk_index: 1,
      content: "Approval is required.",
    }],
    ...overrides,
  };
}

function sse(event: string, payload: unknown): Uint8Array {
  return new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`);
}

export function installMockApi(options: MockApiOptions = {}) {
  const calls: Array<{ path: string; method: string; body?: unknown }> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://openweight.test");
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ path: `${url.pathname}${url.search}`, method, body });

    let status = 200;
    let payload: unknown;
    if (url.pathname === "/healthz") {
      payload = { request_id: "req-health", status: "alive", service: "openweight-platform" };
    } else if (url.pathname === "/readyz") {
      payload = {
        request_id: "req-ready",
        status: "ready",
        dependencies: [{ name: "postgres", status: "ready", required: true, detail: "available" }],
      };
    } else if (url.pathname === "/v1/service-info") {
      payload = {
        request_id: "req-info",
        service: "openweight-platform",
        version: "0.1.0",
        environment: "demo",
        deployment_profile: "local",
        backend: "fake",
        model_id: "openai/gpt-oss-20b",
        inference_provider: "local_transformers",
        inference_configured: true,
        inference_state: "not_initialized",
        demo_state: "durable",
        build_sha: "1234567890abcdef",
        ...options.serviceInfo,
      };
    } else if (url.pathname === "/v1/access-requests") {
      status = options.listStatus ?? 200;
      payload = status === 200
        ? { request_id: "req-list", access_requests: [FICTIONAL_ACCESS_REQUEST] }
        : { status: "error", error: { code: "dependency_unavailable", message: "raw-private-detail" } };
    } else if (url.pathname === "/v1/policy/query/stream") {
      status = options.queryStatus ?? 200;
      if (status !== 200) {
        payload = {
          status: "error",
          error: {
            code: status === 429 ? "public_demo_busy" : "backend_unavailable",
            message: "raw-private-detail",
          },
        };
      } else {
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(sse("stage", { stage: "searching" }));
            void (async () => {
              if (options.queryGate) await options.queryGate;
              controller.enqueue(sse("stage", { stage: "generating" }));
              if (options.queryStreamHiddenText) {
                controller.enqueue(sse("reasoning", { text: options.queryStreamHiddenText }));
              }
              for (const text of options.queryDeltas ?? ["Privileged access requires explicit ", "approval and evidence [EPG-ACCESS-001#1]."]) {
                controller.enqueue(sse("answer_delta", { text }));
              }
              if (options.queryCompletionGate) await options.queryCompletionGate;
              if (options.queryStreamError) {
                controller.enqueue(sse("error", { error: { code: "dependency_unavailable", message: "raw-private-detail" } }));
              } else {
                controller.enqueue(sse("complete", policyResult(options.queryResult)));
              }
              controller.close();
            })();
          },
        });
        return new Response(stream, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        });
      }
    } else if (url.pathname === "/v1/policy/query") {
      if (options.queryGate) await options.queryGate;
      status = options.queryStatus ?? 200;
      payload = status === 200
        ? policyResult(options.queryResult)
        : { status: "error", error: { code: "backend_unavailable", message: "raw-private-detail" } };
    } else if (url.pathname.endsWith("/proposals")) {
      payload = {
        request_id: "req-proposal",
        status: "approval_required",
        approval_required: true,
        approval_id: "approval-fixture-001",
        proposal: {
          action_type: "set_access_request_status",
          summary: "Set fic-req-002 to approved",
          arguments: { access_request_id: "fic-req-002", new_status: "approved" },
          consequence: "Changes the fictional access-request status after approval.",
        },
      };
    } else if (url.pathname.endsWith("/resume")) {
      status = options.resumeStatus ?? 200;
      payload = status === 200
        ? {
            request_id: "req-resume",
            approval_id: "approval-fixture-001",
            status: body?.decision === "reject" ? "rejected" : "approved",
            action_result: body?.decision === "reject" ? null : { result: "updated" },
          }
        : { status: "error", error: { code: "state_conflict", message: "internal replay detail" } };
    } else {
      status = 404;
      payload = { status: "error", error: { code: "not_found", message: "not found" } };
    }

    return new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}
