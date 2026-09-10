import {
  ArrowRight,
  BookOpenCheck,
  CheckSquare2,
  ClipboardList,
  Clock3,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { AccessRequestRecord } from "../api/types";
import { EmptyState, ErrorState, LoadingState } from "../components/StatePanel";
import { StatusBadge } from "../components/StatusBadge";
import { useApproval } from "../context/ApprovalContext";
import { useDemoPersona, type Persona } from "../context/DemoPersonaContext";

type TaskKey = "policy" | "requests" | "approvals";

const PRIORITY_TASK: Record<Persona, TaskKey> = {
  Reader: "policy",
  Approver: "approvals",
  Operator: "requests",
};

const TASKS = {
  policy: {
    eyebrow: "Policy & Evidence",
    title: "Get AI-assisted answers grounded in company policy and cited evidence.",
    action: "Ask a policy question",
    to: "/assistant",
    icon: BookOpenCheck,
  },
  requests: {
    eyebrow: "Access Requests",
    title: "Review employee and contractor access requests and proposed changes.",
    action: "View access requests",
    to: "/access-requests",
    icon: ClipboardList,
  },
  approvals: {
    eyebrow: "Approvals",
    title: "Review controlled actions waiting for human authorization.",
    action: "Review approvals",
    to: "/approvals",
    icon: CheckSquare2,
  },
} as const;

function personLabel(record: AccessRequestRecord): string {
  return `${record.subject_id} · ${record.subject_type}`;
}

export function OverviewPage() {
  const [records, setRecords] = useState<AccessRequestRecord[]>([]);
  const [recordsLoading, setRecordsLoading] = useState(true);
  const [recordsError, setRecordsError] = useState(false);
  const [availability, setAvailability] = useState<"checking" | "available" | "degraded">("checking");
  const { persona, can } = useDemoPersona();
  const { pending, outcome } = useApproval();

  useEffect(() => {
    const controller = new AbortController();
    api.listAccessRequests(undefined, controller.signal)
      .then((response) => setRecords(response.access_requests))
      .catch(() => {
        if (!controller.signal.aborted) setRecordsError(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setRecordsLoading(false);
      });

    Promise.all([api.health(controller.signal), api.readiness(controller.signal)])
      .then(([, readiness]) => {
        setAvailability(readiness.status === "ready" ? "available" : "degraded");
      })
      .catch(() => {
        if (!controller.signal.aborted) setAvailability("degraded");
      });
    return () => controller.abort();
  }, []);

  const orderedTasks = (["policy", "requests", "approvals"] as const).map((key) => ({
    ...TASKS[key],
    priority: key === PRIORITY_TASK[persona],
  }));
  const pendingRequests = useMemo(
    () => records.filter((record) => record.approval_status === "pending").slice(0, 3),
    [records],
  );
  const pendingApproval = pending && !outcome ? pending : null;

  return (
    <div className="page page--overview">
      <section className="workspace-intro" aria-labelledby="workspace-heading">
        <div>
          <p className="eyebrow">OpenWeight</p>
          <h1 id="workspace-heading">Governance workspace</h1>
          <p className="workspace-intro__lede">
            Review policy evidence, manage access requests, and authorize controlled actions.
          </p>
          <p className="workspace-intro__tagline">Answers with provenance. Actions with human control.</p>
        </div>
        <div className={`availability-pill availability-pill--${availability}`} role="status">
          <span aria-hidden="true" />
          {availability === "checking"
            ? "Checking service availability…"
            : availability === "available"
              ? "Access governance available"
              : "Service availability not confirmed"}
        </div>
      </section>

      <section aria-labelledby="primary-actions-heading">
        <div className="section-heading section-heading--compact">
          <div>
            <p className="eyebrow">Primary tasks</p>
            <h2 id="primary-actions-heading">What would you like to do?</h2>
          </div>
          <span className="role-context">Prioritized for {persona}</span>
        </div>
        <div className="task-grid">
          {orderedTasks.map(({ eyebrow, title, action, to, icon: Icon, priority }) => (
            <article className={`task-card ${priority ? "task-card--priority" : ""}`} key={to}>
              <span className="task-card__icon"><Icon aria-hidden="true" /></span>
              <p className="task-card__eyebrow">{eyebrow}</p>
              <h3>{title}</h3>
              <Link to={to}>{action}<ArrowRight aria-hidden="true" /></Link>
            </article>
          ))}
        </div>
      </section>

      <section aria-labelledby="your-work-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Current activity</p>
            <h2 id="your-work-heading">Your work</h2>
          </div>
        </div>

        {can("approve") ? (
          pendingApproval ? (
            <div className="attention-card">
              <span className="attention-card__icon"><Clock3 aria-hidden="true" /></span>
              <div>
                <strong>Approval waiting for review</strong>
                <p>{pendingApproval.accessRequest.request_id}: {pendingApproval.accessRequest.approval_status} → {pendingApproval.proposedStatus}</p>
              </div>
              <Link className="button button--primary" to="/approvals">Review approval</Link>
            </div>
          ) : (
            <div className="work-empty-inline">
              <CheckSquare2 aria-hidden="true" />
              <div><strong>No approvals waiting</strong><p>You're up to date. New proposals created in this session will appear here.</p></div>
            </div>
          )
        ) : null}

        <div className="work-panel">
          <div className="work-panel__header">
            <div>
              <h3>Pending access requests</h3>
              <p>Review requests that have not yet reached a decision.</p>
            </div>
            {!recordsLoading && !recordsError ? <span>{pendingRequests.length} shown</span> : null}
          </div>
          {recordsLoading ? <LoadingState label="Loading access requests…" /> : null}
          {recordsError ? (
            <ErrorState
              title="We couldn't load access requests"
              message="Try again or open Access Requests to refresh the current records."
              action={<Link to="/access-requests">Open Access Requests</Link>}
            />
          ) : null}
          {!recordsLoading && !recordsError && pendingRequests.length === 0 ? (
            <EmptyState title="No pending access requests" message="There are no pending requests in the current data." />
          ) : null}
          {!recordsLoading && !recordsError && pendingRequests.length > 0 ? (
            <div className="work-list" role="list" aria-label="Pending access requests">
              {pendingRequests.map((record) => (
                <article className="work-row" role="listitem" key={record.request_id}>
                  <div className="work-row__request"><strong>{record.request_id}</strong><span>{personLabel(record)}</span></div>
                  <div><span>Resource</span><strong>{record.system_name}</strong></div>
                  <div><span>Requested role</span><strong>{record.requested_role}</strong></div>
                  <StatusBadge label={record.approval_status} tone={record.approval_status} />
                  <Link
                    to="/access-requests"
                    state={{ selectedRequestId: record.request_id }}
                    aria-label={`Review ${record.request_id}`}
                  >
                    Review <ArrowRight aria-hidden="true" />
                  </Link>
                </article>
              ))}
            </div>
          ) : null}
          <div className="work-panel__footer">
            <Link to="/access-requests">View all access requests <ArrowRight aria-hidden="true" /></Link>
          </div>
        </div>
      </section>
    </div>
  );
}
