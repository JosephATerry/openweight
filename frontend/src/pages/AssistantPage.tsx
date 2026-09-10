import {
  BookOpenCheck,
  CheckCircle2,
  CornerDownLeft,
  FileSearch,
  Sparkles,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { PolicyQueryResponse } from "../api/types";
import { ErrorState } from "../components/StatePanel";
import { PolicyAnswer, PolicyCitationLink } from "../components/PolicyAnswer";
import type { PolicyCitation } from "../components/PolicyAnswer";

const SUGGESTED_QUERIES = [
  "What policy evidence is required before approving privileged access?",
  "Summarize the MFA requirements for contractor access.",
  "Which controls govern time-bounded access requests?",
];

export function AssistantPage() {
  const [question, setQuestion] = useState("");
  const [response, setResponse] = useState<PolicyQueryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [stage, setStage] = useState<"searching" | "generating" | null>(null);
  const [streamedAnswer, setStreamedAnswer] = useState("");
  const activeRequest = useRef<AbortController | null>(null);

  useEffect(() => () => activeRequest.current?.abort(), []);

  const supportingEvidence = response?.citation_valid
    ? response.citations.flatMap((id) => {
      const evidence = response.evidence.find((item) => item.citation_id === id);
      return evidence ? [evidence] : [];
    }) : [];
  const validatedCitations: PolicyCitation[] = supportingEvidence.map((evidence, index) => ({
    id: evidence.citation_id,
    policyId: evidence.policy_id,
    title: evidence.title,
    targetId: `policy-evidence-${index}`,
  }));

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const normalized = question.trim();
    if (!normalized || submitting) return;
    setSubmitting(true);
    setError(null);
    setResponse(null);
    setStage("searching");
    setStreamedAnswer("");
    const controller = new AbortController();
    activeRequest.current = controller;
    try {
      const completed = await api.streamPolicy(
        normalized,
        {
          onStage: setStage,
          onAnswerDelta: (text) => setStreamedAnswer((current) => current + text),
        },
        controller.signal,
      );
      setResponse(completed);
      setStreamedAnswer(completed.answer);
      setStage(null);
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setStreamedAnswer("");
      setStage(null);
      setError(reason instanceof ApiError && reason.status === 429
        ? "The public demo is receiving several requests. Please try again shortly."
        : reason instanceof ApiError && reason.status === 503
          ? "Policy evidence or the configured inference service is temporarily unavailable. Access requests and approvals remain available."
          : "We couldn't answer this policy question. Try again.");
    } finally {
      if (activeRequest.current === controller) activeRequest.current = null;
      setSubmitting(false);
    }
  }

  return (
    <div className="page page--assistant">
      <div className="page-heading page-heading--split">
        <div>
          <p className="eyebrow">Policy &amp; evidence</p>
          <h1>Ask a policy question</h1>
          <p>Get AI-assisted answers grounded in company policy and cited evidence.</p>
        </div>
        <div className="trust-note">
          <BookOpenCheck aria-hidden="true" />
          <div><strong>Internal policy &amp; evidence</strong><span>Sources stay connected to the guidance they support.</span></div>
        </div>
      </div>

      <section className="assistant-workspace" aria-labelledby="question-heading">
        <div className="query-panel">
          <div className="query-panel__heading">
            <span className="query-panel__step">01</span>
            <div><h2 id="question-heading">What do you need to know?</h2><p>Ask a question about company policy or access-governance records. Suggested questions are optional examples.</p></div>
          </div>
          <form onSubmit={submit}>
            <label htmlFor="agent-question">Question</label>
            <div className="query-composer">
              <textarea
                id="agent-question"
                value={question}
                maxLength={4000}
                rows={5}
                placeholder="Ask about company policy, access controls, or evidence…"
                onChange={(event) => setQuestion(event.target.value)}
                disabled={submitting}
              />
              <div className="query-composer__footer">
                <span>{question.length.toLocaleString()} / 4,000</span>
                <button className="button button--primary" type="submit" disabled={!question.trim() || submitting}>
                  {submitting ? (stage === "searching" ? "Searching policy…" : "Generating answer…") : "Ask policy question"}
                  <CornerDownLeft aria-hidden="true" />
                </button>
              </div>
            </div>
          </form>
          <div className="suggestion-list" aria-label="Suggested questions">
            <span>Suggested questions</span>
            {SUGGESTED_QUERIES.map((suggestion) => (
              <button key={suggestion} type="button" disabled={submitting} onClick={() => setQuestion(suggestion)}>
                <Sparkles aria-hidden="true" />{suggestion}
              </button>
            ))}
          </div>
        </div>

        <div className="answer-panel" aria-live="polite">
          <div className="query-panel__heading">
            <span className="query-panel__step">02</span>
            <div><h2>Review the result</h2><p>Answer and evidence remain visibly distinct.</p></div>
          </div>
          {submitting && !streamedAnswer ? (
            <div className="analysis-state" role="status">
              <span className="analysis-state__pulse"><FileSearch aria-hidden="true" /></span>
              <strong>{stage === "generating" ? "Generating a grounded answer…" : "Searching company policy…"}</strong>
              <p>{stage === "generating" ? "Relevant evidence is ready. OpenWeight is composing an employee-facing answer." : "Finding relevant authorized internal sources. This can take a moment."}</p>
              <div className="analysis-state__line" />
              <div className="analysis-state__line analysis-state__line--short" />
            </div>
          ) : null}
          {submitting && streamedAnswer ? (
            <div className="answer-result answer-result--streaming" role="status">
              <div className="answer-result__meta">
                <span><Sparkles aria-hidden="true" /> Generating answer</span>
                <span>Citation validation pending</span>
              </div>
              <h3>AI-generated answer</h3>
              <PolicyAnswer text={streamedAnswer} streaming />
              <p className="answer-guidance">Supporting evidence and validated citations will appear when generation is complete.</p>
            </div>
          ) : null}
          {error ? (
            <ErrorState
              title="AI Evidence is unavailable right now"
              message={error}
              action={<><button type="button" onClick={() => void submit()}>Try again</button><Link to="/access-requests">Review access requests</Link></>}
            />
          ) : null}
          {!submitting && !error && !response ? (
            <div className="answer-empty">
              <FileSearch aria-hidden="true" />
              <strong>Your evidence review will appear here</strong>
              <p>Type your question or edit one of the suggested examples.</p>
            </div>
          ) : null}
          {response ? (
            <div className="answer-result">
              <div className="answer-result__meta">
                <span><CheckCircle2 aria-hidden="true" /> {response.status === "answered" ? "Answer ready" : "Evidence insufficient"}</span>
                <span>Internal policy &amp; evidence</span>
              </div>
              <h3>AI-generated answer</h3>
              <PolicyAnswer text={response.answer} citations={validatedCitations} />
              {validatedCitations.length ? (
                <div className="citation-summary" aria-label="Citations used">
                  <strong>Citations used</strong>
                  {validatedCitations.map((citation) => <PolicyCitationLink key={citation.id} citation={citation} />)}
                  {response.citation_valid ? <small>Citations validated against retrieved evidence</small> : null}
                  <small>Citation matching confirms the source reference, not the accuracy of each claim.</small>
                </div>
              ) : null}
              <p className="answer-guidance">AI-generated answers are grounded in retrieved company policy. Review cited evidence before making decisions.</p>
              <div className="evidence-section">
                <div className="evidence-section__heading">
                  <h3>Supporting evidence</h3>
                  <span>{supportingEvidence.length} item{supportingEvidence.length === 1 ? "" : "s"}</span>
                </div>
                {supportingEvidence.length ? (
                  <ol className="evidence-list">
                    {supportingEvidence.map((item, index) => (
                      <li key={item.citation_id} id={`policy-evidence-${index}`} tabIndex={-1}>
                        <span>{String(index + 1).padStart(2, "0")}</span>
                        <div>
                          <strong>{item.title}</strong>
                          <div className="evidence-list__metadata">
                            <span>{item.policy_id}</span>
                            <span>{item.domain.replaceAll("-", " ")}</span>
                            <span>{item.citation_id}</span>
                          </div>
                          <PolicyAnswer text={item.content} evidence />
                        </div>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="muted-copy">No supporting internal policy evidence was found for this question.</p>
                )}
              </div>
            </div>
          ) : null}
        </div>
      </section>
    </div>
  );
}
