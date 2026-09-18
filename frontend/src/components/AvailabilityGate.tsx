import { CheckCircle2, LoaderCircle, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { api, subscribeBackendUnavailable } from "../api/client";

type AvailabilityPhase = "backend" | "preparing" | "ready" | "unavailable";

const DEFAULT_TIMEOUT_MS = 120_000;
const DEFAULT_POLL_DELAYS_MS = [1_500, 2_500, 5_000] as const;

function wait(delayMs: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, delayMs);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    }, { once: true });
  });
}

export function AvailabilityGate({
  children,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  pollDelaysMs = DEFAULT_POLL_DELAYS_MS,
}: {
  children: ReactNode;
  timeoutMs?: number;
  pollDelaysMs?: readonly number[];
}) {
  const [phase, setPhase] = useState<AvailabilityPhase>("backend");
  const [attempt, setAttempt] = useState(0);
  const phaseRef = useRef(phase);

  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  useEffect(() => subscribeBackendUnavailable(() => {
    if (phaseRef.current === "ready") {
      setPhase("backend");
      setAttempt((value) => value + 1);
    }
  }), []);

  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    let active = true;
    let completed = false;

    async function prepare(): Promise<void> {
      setPhase("backend");
      try {
        await api.health(controller.signal);
        if (!active) return;
        setPhase("preparing");
        let poll = 0;
        while (!controller.signal.aborted) {
          try {
            const readiness = await api.readiness(controller.signal);
            if (readiness.status === "ready") {
              completed = true;
              if (active) setPhase("ready");
              return;
            }
          } catch (error) {
            if (error instanceof DOMException && error.name === "AbortError") throw error;
            if (active) setPhase("backend");
          }
          const delay = pollDelaysMs[Math.min(poll, pollDelaysMs.length - 1)] ?? 5_000;
          poll += 1;
          await wait(delay, controller.signal);
          if (active && phaseRef.current === "backend") setPhase("preparing");
        }
      } catch {
        // Only sanitized state is rendered below.
      } finally {
        window.clearTimeout(timeout);
        if (active && !completed) setPhase("unavailable");
      }
    }

    void prepare();
    return () => {
      active = false;
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [attempt, pollDelaysMs, timeoutMs]);

  if (phase === "ready") return <>{children}</>;

  const preparing = phase === "preparing";
  const unavailable = phase === "unavailable";
  return (
    <main className="availability-gate" aria-labelledby="availability-heading">
      <section className="availability-card">
        <p className="eyebrow">OpenWeight</p>
        <h1 id="availability-heading">Preparing OpenWeight</h1>
        <p>Starting the AI and retrieval service. The first request after an idle period may take a moment.</p>
        <ol className="availability-steps" aria-live="polite">
          <li className="availability-step availability-step--complete"><CheckCircle2 aria-hidden="true" /><span>Frontend ready</span></li>
          <li className={`availability-step ${preparing ? "availability-step--complete" : "availability-step--active"}`}>
            {preparing ? <CheckCircle2 aria-hidden="true" /> : <LoaderCircle aria-hidden="true" />}
            <span>Backend starting</span>
          </li>
          <li className={`availability-step ${preparing ? "availability-step--active" : ""}`}>
            {preparing ? <LoaderCircle aria-hidden="true" /> : <span aria-hidden="true" className="availability-dot" />}
            <span>AI and retrieval service preparing</span>
          </li>
          <li className="availability-step"><span aria-hidden="true" className="availability-dot" /><span>Ready</span></li>
        </ol>
        {unavailable ? (
          <div className="availability-error" role="alert">
            <strong>OpenWeight is taking longer than expected to start.</strong>
            <p>No request was submitted. Try preparing the service again.</p>
            <button className="button button--primary" type="button" onClick={() => setAttempt((value) => value + 1)}>
              <RotateCcw aria-hidden="true" /> Try again
            </button>
          </div>
        ) : null}
      </section>
    </main>
  );
}
