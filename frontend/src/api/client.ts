import type {
  AccessRequestDetailResponse,
  AccessRequestListResponse,
  AgentQueryResponse,
  ApprovalDecision,
  ApprovalProposalResponse,
  ApprovalResumeResponse,
  ApprovalStatus,
  HealthResponse,
  PolicyQueryResponse,
  ReadinessResponse,
  ServiceInfoResponse,
} from "./types";

type RequestOptions = Omit<RequestInit, "body"> & { body?: unknown };

export interface PolicyStreamHandlers {
  onStage?: (stage: "searching" | "generating") => void;
  onAnswerDelta?: (text: string) => void;
}

const ERROR_MESSAGES: Record<number, string> = {
  400: "The request could not be accepted. Review the supplied values.",
  401: "Authentication is required for this operation.",
  403: "Your current role is not authorized for this operation.",
  404: "The requested record is no longer available.",
  409: "This approval state has already changed. Refresh before continuing.",
  429: "The public demo is receiving several requests. Please try again shortly.",
  422: "The submitted information does not match the required format.",
  500: "The service could not complete the request.",
  503: "A required service dependency is temporarily unavailable.",
};

let sessionAccessToken: string | null = null;
const unavailableListeners = new Set<() => void>();

export function subscribeBackendUnavailable(listener: () => void): () => void {
  unavailableListeners.add(listener);
  return () => unavailableListeners.delete(listener);
}

function reportBackendUnavailable(status: number): void {
  if (status === 0 || status === 502 || status === 503 || status === 504) {
    unavailableListeners.forEach((listener) => listener());
  }
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export function setSessionAccessToken(token: string | null): void {
  const normalized = token?.trim();
  sessionAccessToken = normalized ? normalized : null;
}

function configuredBaseUrl(): string {
  const value = import.meta.env.VITE_API_BASE_URL?.trim() ?? "";
  if (!value) return "";
  const parsed = new URL(value, window.location.origin);
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) {
    throw new Error("VITE_API_BASE_URL must be a credential-free HTTP(S) URL");
  }
  return value.replace(/\/$/, "");
}

function requestId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `web-${Date.now().toString(36)}`;
}

function requestHeaders(accept: string, hasBody: boolean): Headers {
  const headers = new Headers();
  headers.set("Accept", accept);
  headers.set("X-Request-ID", requestId());
  if (hasBody) headers.set("Content-Type", "application/json");
  if (sessionAccessToken) headers.set("Authorization", `Bearer ${sessionAccessToken}`);
  return headers;
}

async function apiError(response: Response): Promise<ApiError> {
  let code = "request_failed";
  try {
    const body = (await response.json()) as {
      error?: { code?: unknown };
    };
    if (typeof body.error?.code === "string") code = body.error.code;
  } catch {
    // Client errors remain intentionally independent of raw response content.
  }
  return new ApiError(
    response.status,
    code,
    ERROR_MESSAGES[response.status] ?? "The request could not be completed.",
  );
}

async function request<T>(
  path: string,
  options: RequestOptions = {},
  acceptedStatuses: readonly number[] = [],
): Promise<T> {
  const headers = requestHeaders("application/json", options.body !== undefined);
  new Headers(options.headers).forEach((value, key) => headers.set(key, value));

  let response: Response;
  try {
    response = await fetch(`${configuredBaseUrl()}${path}`, {
      ...options,
      headers,
      credentials: "same-origin",
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    reportBackendUnavailable(0);
    throw new ApiError(0, "network_error", "The service could not be reached.");
  }

  if (!response.ok && !acceptedStatuses.includes(response.status)) {
    reportBackendUnavailable(response.status);
    throw await apiError(response);
  }

  return (await response.json()) as T;
}

async function streamPolicy(
  question: string,
  handlers: PolicyStreamHandlers,
  signal?: AbortSignal,
): Promise<PolicyQueryResponse> {
  let response: Response;
  try {
    response = await fetch(`${configuredBaseUrl()}/v1/policy/query/stream`, {
      method: "POST",
      headers: requestHeaders("text/event-stream", true),
      credentials: "same-origin",
      body: JSON.stringify({ question }),
      signal,
    });
  } catch (reason) {
    if (reason instanceof DOMException && reason.name === "AbortError") throw reason;
    reportBackendUnavailable(0);
    throw new ApiError(0, "network_error", "The service could not be reached.");
  }
  if (!response.ok) {
    reportBackendUnavailable(response.status);
    throw await apiError(response);
  }
  if (!response.body) {
    throw new ApiError(0, "stream_unavailable", "The response stream is unavailable.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed: PolicyQueryResponse | null = null;

  function consume(frame: string): void {
    const lines = frame.split("\n");
    const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
    const data = lines
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!event || !data) return;
    const payload = JSON.parse(data) as Record<string, unknown>;
    if (event === "stage") {
      if (payload.stage === "searching" || payload.stage === "generating") {
        handlers.onStage?.(payload.stage);
      }
    } else if (event === "answer_delta") {
      if (typeof payload.text === "string") handlers.onAnswerDelta?.(payload.text);
    } else if (event === "complete") {
      completed = payload as unknown as PolicyQueryResponse;
    } else if (event === "error") {
      const detail = payload.error;
      const code = typeof detail === "object" && detail !== null &&
        typeof (detail as Record<string, unknown>).code === "string"
        ? String((detail as Record<string, unknown>).code)
        : "stream_failed";
      const status = code === "public_demo_busy"
        ? 429
        : code === "dependency_unavailable" ? 503 : 500;
      reportBackendUnavailable(status);
      throw new ApiError(
        status,
        code,
        ERROR_MESSAGES[status] ?? "The request could not be completed.",
      );
    }
  }

  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replaceAll("\r\n", "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        consume(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");
      }
      if (done) break;
    }
    if (buffer.trim()) consume(buffer);
  } catch (reason) {
    await reader.cancel().catch(() => undefined);
    if (reason instanceof ApiError) throw reason;
    if (reason instanceof DOMException && reason.name === "AbortError") throw reason;
    throw new ApiError(0, "stream_interrupted", "The response stream was interrupted.");
  }

  if (!completed) {
    throw new ApiError(0, "stream_incomplete", "The response stream ended before completion.");
  }
  return completed;
}

export const api = {
  health: (signal?: AbortSignal) =>
    request<HealthResponse>("/healthz", { signal }),
  readiness: (signal?: AbortSignal) =>
    request<ReadinessResponse>("/readyz", { signal }, [503]),
  serviceInfo: (signal?: AbortSignal) =>
    request<ServiceInfoResponse>("/v1/service-info", { signal }),
  query: (question: string, signal?: AbortSignal) =>
    request<AgentQueryResponse>("/v1/agent/query", {
      method: "POST",
      body: { question },
      signal,
    }),
  queryPolicy: (question: string, signal?: AbortSignal) =>
    request<PolicyQueryResponse>("/v1/policy/query", {
      method: "POST",
      body: { question },
      signal,
    }),
  streamPolicy,
  listAccessRequests: (status?: ApprovalStatus, signal?: AbortSignal) => {
    const params = new URLSearchParams({ limit: "50" });
    if (status) params.set("approval_status", status);
    return request<AccessRequestListResponse>(
      `/v1/access-requests?${params.toString()}`,
      { signal },
    );
  },
  getAccessRequest: (requestIdValue: string, signal?: AbortSignal) =>
    request<AccessRequestDetailResponse>(
      `/v1/access-requests/${encodeURIComponent(requestIdValue)}`,
      { signal },
    ),
  proposeStatus: (requestIdValue: string, newStatus: ApprovalStatus) =>
    request<ApprovalProposalResponse>(
      `/v1/actions/access-requests/${encodeURIComponent(requestIdValue)}/proposals`,
      { method: "POST", body: { new_status: newStatus } },
    ),
  resumeApproval: (
    approvalId: string,
    decision: ApprovalDecision,
    comment?: string,
  ) =>
    request<ApprovalResumeResponse>(
      `/v1/approvals/${encodeURIComponent(approvalId)}/resume`,
      {
        method: "POST",
        body: { decision, comment: comment?.trim() || null },
      },
    ),
};
