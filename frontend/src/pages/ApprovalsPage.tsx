import {
  ArrowLeft,
  Check,
  CheckCircle2,
  Clock3,
  Fingerprint,
  ShieldCheck,
  X,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { ApprovalDecision } from "../api/types";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { EmptyState } from "../components/StatePanel";
import { StatusBadge } from "../components/StatusBadge";
import { useApproval } from "../context/ApprovalContext";
import { useDemoPersona } from "../context/DemoPersonaContext";

export function ApprovalsPage() {
  const { pending, outcome, setOutcome, reset } = useApproval();
  const { persona, can } = useDemoPersona();
  const [decision, setDecision] = useState<ApprovalDecision | null>(null);
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirmDecision() {
    if (!pending || !decision || submitting || outcome) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = await api.resumeApproval(
        pending.response.approval_id,
        decision,
        comment,
      );
      setOutcome(response);
      setDecision(null);
    } catch (reason) {
      setDecision(null);
      setError(reason instanceof ApiError && reason.status === 409
        ? "This approval is no longer pending. Refresh the request to view its current state."
        : reason instanceof ApiError ? reason.message : "We couldn't record this approval decision. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="page">
      <div className="page-heading page-heading--split">
        <div>
          <p className="eyebrow">Human approval workspace</p>
          <h1>Review approvals</h1>
          <p>Review proposed access changes and make an explicit approval or rejection decision.</p>
        </div>
        <div className="role-callout">
          <span>Viewing as</span><strong>{persona}</strong><small>Backend permissions remain authoritative.</small>
        </div>
      </div>

      {!pending ? (
        <div className="empty-approval-card">
          <EmptyState title="No approvals waiting" message="You're up to date. New proposals requiring your review will appear here." />
          <Link className="button button--secondary" to="/access-requests"><ArrowLeft aria-hidden="true" /> View access requests</Link>
        </div>
      ) : (
        <div className="approval-layout">
          <section className="approval-card">
            <div className="approval-card__status">
              <StatusBadge label={outcome?.status ?? "pending review"} tone={outcome?.status === "approved" ? "approved" : outcome?.status === "rejected" ? "denied" : "pending"} />
              <span><Clock3 aria-hidden="true" /> Awaiting your decision</span>
            </div>
            <div className="approval-card__heading">
              <span className="approval-card__icon"><ShieldCheck aria-hidden="true" /></span>
              <div><p className="eyebrow">Access request</p><h2>{pending.accessRequest.request_id}</h2></div>
            </div>

            <div className="change-comparison" aria-label="Proposed status change">
              <div><span>Current status</span><strong>{pending.accessRequest.approval_status}</strong></div>
              <span className="change-comparison__arrow" aria-hidden="true">→</span>
              <div className="change-comparison__target"><span>Proposed status</span><strong>{pending.proposedStatus}</strong></div>
            </div>

            <dl className="approval-facts">
              <div><dt>Request</dt><dd>{pending.accessRequest.request_id}</dd></div>
              <div><dt>System</dt><dd>{pending.accessRequest.system_name}</dd></div>
              <div><dt>Identity</dt><dd>{pending.accessRequest.subject_id}</dd></div>
              <div><dt>Role</dt><dd>{pending.accessRequest.requested_role}</dd></div>
            </dl>

            <div className="consequence-note"><strong>Requested action</strong><p>Change the access request from {pending.accessRequest.approval_status} to {pending.proposedStatus}.</p></div>
            <div className="approval-identity"><Fingerprint aria-hidden="true" /><div><span>Approval ID</span><code>{pending.response.approval_id}</code></div></div>

            {!outcome ? (
              <div className="decision-form">
                <label htmlFor="approval-comment">Review comment <span>optional</span></label>
                <textarea id="approval-comment" rows={3} maxLength={1000} value={comment} placeholder="Add concise context for the audit trail…" onChange={(event) => setComment(event.target.value)} />
                {can("approve") ? (
                  <div className="decision-actions">
                    <button className="button button--quiet" type="button" disabled={submitting} onClick={() => setDecision("reject")}><X aria-hidden="true" /> Reject</button>
                    <button className="button button--primary" type="button" disabled={submitting} onClick={() => setDecision("approve")}><Check aria-hidden="true" /> Approve</button>
                  </div>
                ) : (
                  <div className="permission-note">Approval decisions require the Approver or Operator role.</div>
                )}
                {error ? <p className="form-error" role="alert">{error}</p> : null}
              </div>
            ) : (
              <div className={`approval-outcome approval-outcome--${outcome.status}`} role="status">
                <CheckCircle2 aria-hidden="true" />
                <div><strong>{outcome.status === "approved" ? "Status change completed" : "Proposal rejected"}</strong><p>{outcome.status === "approved" ? `The access request was changed to ${pending.proposedStatus} after your confirmation.` : "No access change was made."}</p><small>Request {outcome.request_id}</small></div>
              </div>
            )}
          </section>

          <aside className="guardrail-rail">
            <p className="eyebrow">Approval steps</p>
            <ol>
              <li className="guardrail-rail__complete"><span>1</span><div><strong>Proposal created</strong><small>The requested change was recorded</small></div></li>
              <li className="guardrail-rail__complete"><span>2</span><div><strong>Details reviewed</strong><small>The request and proposed status are clear</small></div></li>
              <li className={outcome ? "guardrail-rail__complete" : "guardrail-rail__active"}><span>3</span><div><strong>Human decision</strong><small>Explicit approval or rejection</small></div></li>
              <li className={outcome?.status === "approved" ? "guardrail-rail__complete" : ""}><span>4</span><div><strong>Status changed</strong><small>Only after confirmed approval</small></div></li>
            </ol>
            {outcome ? <button className="button button--quiet button--full" type="button" onClick={reset}>Clear completed review</button> : null}
          </aside>
        </div>
      )}

      <ConfirmDialog
        open={decision !== null}
        title={
          decision === "approve"
            ? "Approve this status change?"
            : "Reject this proposal?"
        }
        description={decision === "approve" ? `You are approving a controlled change to access request ${pending?.accessRequest.request_id ?? "the selected request"}. ${pending?.accessRequest.approval_status ?? "Current state"} → ${pending?.proposedStatus ?? "proposed state"}. This action will execute only after confirmation.` : `You are rejecting the proposed change to access request ${pending?.accessRequest.request_id ?? "the selected request"}. No access change will be made.`}
        confirmLabel={decision === "approve" ? "Confirm approval" : "Confirm rejection"}
        tone={decision === "reject" ? "danger" : "primary"}
        busy={submitting}
        onCancel={() => !submitting && setDecision(null)}
        onConfirm={confirmDecision}
      />
    </div>
  );
}
