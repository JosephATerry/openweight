import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import { AvailabilityGate } from "../components/AvailabilityGate";

function json(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function health(): Response {
  return json({ request_id: "health", status: "alive", service: "openweight-platform" });
}

function readiness(ready: boolean): Response {
  return json({
    request_id: "ready",
    status: ready ? "ready" : "not_ready",
    dependencies: [{
      name: "retrieval_encoder",
      status: ready ? "ready" : "unavailable",
      required: true,
      detail: ready ? "local retrieval encoder ready" : "local retrieval encoder initializing",
    }],
  }, ready ? 200 : 503);
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("cold-start availability gate", () => {
  it("renders before the application and issues exactly one initial wake request", async () => {
    let rejectWake: ((reason?: unknown) => void) | undefined;
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      rejectWake = reject;
      init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    }));
    vi.stubGlobal("fetch", fetchMock);

    const view = render(<AvailabilityGate timeoutMs={10_000}><div>Application ready</div></AvailabilityGate>);
    expect(screen.getByRole("heading", { name: "Preparing OpenWeight" })).toBeInTheDocument();
    expect(screen.getByText("Backend starting")).toBeInTheDocument();
    expect(screen.queryByText("Application ready")).not.toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/healthz");
    view.unmount();
    rejectWake?.(new DOMException("Aborted", "AbortError"));
  });

  it("polls readiness serially and enables the application only when ready", async () => {
    let readinessCalls = 0;
    let resolveFirstReadiness: ((response: Response) => void) | undefined;
    let readinessActive = 0;
    let maxReadinessActive = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/healthz")) return Promise.resolve(health());
      readinessCalls += 1;
      readinessActive += 1;
      maxReadinessActive = Math.max(maxReadinessActive, readinessActive);
      if (readinessCalls === 1) {
        return new Promise<Response>((resolve) => {
          resolveFirstReadiness = (response) => {
            readinessActive -= 1;
            resolve(response);
          };
        });
      }
      readinessActive -= 1;
      return Promise.resolve(readiness(true));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AvailabilityGate timeoutMs={2_000} pollDelaysMs={[1]}><div>Application ready</div></AvailabilityGate>);
    expect(await screen.findByText("AI and retrieval service preparing")).toBeInTheDocument();
    await waitFor(() => expect(readinessCalls).toBe(1));
    await new Promise((resolve) => window.setTimeout(resolve, 5));
    expect(readinessCalls).toBe(1);
    await act(async () => resolveFirstReadiness?.(readiness(false)));
    expect(await screen.findByText("Application ready")).toBeInTheDocument();
    expect(maxReadinessActive).toBe(1);
  });

  it("times out safely and retries only after explicit user action", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    }));
    vi.stubGlobal("fetch", fetchMock);

    render(<AvailabilityGate timeoutMs={20} pollDelaysMs={[1]}><div>Application ready</div></AvailabilityGate>);
    expect(await screen.findByText(/taking longer than expected/i)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("Application ready")).not.toBeInTheDocument();
  });

  it("returns to the gate after backend loss without replaying the failed request", async () => {
    let unavailable = false;
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (unavailable && url.endsWith("/v1/policy/query")) return Promise.reject(new TypeError("offline"));
      if (url.endsWith("/healthz")) return Promise.resolve(health());
      if (url.endsWith("/readyz")) return Promise.resolve(readiness(true));
      return Promise.resolve(json({ request_id: "list", access_requests: [] }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AvailabilityGate timeoutMs={2_000} pollDelaysMs={[1]}><div>Application ready</div></AvailabilityGate>);
    expect(await screen.findByText("Application ready")).toBeInTheDocument();
    unavailable = true;
    await api.queryPolicy("What evidence is required?").catch(() => undefined);
    expect(await screen.findByRole("heading", { name: "Preparing OpenWeight" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/v1/policy/query"))).toHaveLength(1);
  });

  it("uses the configured external API origin for JSON and streaming requests", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.test/");
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(
          'event: complete\ndata: {"request_id":"query","status":"insufficient_evidence","answer":"Insufficient evidence.","citations":[],"citation_valid":true,"evidence":[]}\n\n',
        ));
        controller.close();
      },
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => String(input).endsWith("/healthz")
      ? Promise.resolve(health())
      : Promise.resolve(new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } })));
    vi.stubGlobal("fetch", fetchMock);

    await api.health();
    await api.streamPolicy("What evidence is required?", {});
    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "https://api.example.test/healthz",
      "https://api.example.test/v1/policy/query/stream",
    ]);
  });
});
