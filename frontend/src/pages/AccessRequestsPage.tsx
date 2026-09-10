import {
  ArrowRight,
  CalendarDays,
  ChevronRight,
  Filter,
  RefreshCw,
  ShieldAlert,
  UserRound,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { AccessRequestRecord, ApprovalStatus } from "../api/types";
import { EmptyState, ErrorState, LoadingState } from "../components/StatePanel";
import { StatusBadge } from "../components/StatusBadge";
import { useApproval } from "../context/ApprovalContext";
import { useDemoPersona } from "../context/DemoPersonaContext";

const STATUS_OPTIONS: ApprovalStatus[] = ["pending", "approved", "denied"];

function formatDate(value: string | null): string {
  if (!value) return "No end date";
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

export function AccessRequestsPage() {
  const location = useLocation();
  const requestedSelection = (location.state as { selectedRequestId?: unknown } | null)?.selectedRequestId;
  const [records, setRecords] = useState<AccessRequestRecord[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(
    typeof requestedSelection === "string" ? requestedSelection : null,
  );
  const [filter, setFilter] = useState<ApprovalStatus | "all">("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [proposalStatus, setProposalStatus] = useState<ApprovalStatus>("approved");
  const [proposalBusy, setProposalBusy] = useState(false);
  const [proposalError, setProposalError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const { setPending, pending } = useApproval();
  const { persona, can } = useDemoPersona();
  const navigate = useNavigate();

  useEffect(() => {
    const controller = new AbortController();
    api
      .listAccessRequests(filter === "all" ? undefined : filter, controller.signal)
      .then((response) => {
        setRecords(response.access_requests);
        setSelectedId((current) =>
          response.access_requests.some((item) => item.request_id === current)
            ? current
            : response.access_requests[0]?.request_id ?? null,
        );
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof ApiError && reason.status === 403
            ? reason.message
            : "We couldn't load access requests. Try again.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [filter, refreshKey]);

  const selected = useMemo(
    () => records.find((record) => record.request_id === selectedId) ?? null,
    [records, selectedId],
  );
  const allowedStatuses = useMemo(
    () => selected
      ? STATUS_OPTIONS.filter((status) => status !== selected.approval_status)
      : STATUS_OPTIONS,
    [selected],
  );
  const effectiveProposalStatus = allowedStatuses.includes(proposalStatus)
    ? proposalStatus
    : allowedStatuses[0] ?? "approved";

  async function createProposal() {
    if (!selected || proposalBusy || !can("propose")) return;
    setProposalBusy(true);
    setProposalError(null);
    try {
      const response = await api.proposeStatus(selected.request_id, effectiveProposalStatus);
      setPending({ accessRequest: selected, proposedStatus: effectiveProposalStatus, response });
    } catch (reason) {
      setProposalError(reason instanceof ApiError ? reason.message : "The proposal could not be created.");
    } finally {
      setProposalBusy(false);
    }
  }

  return (
    <div className="page">
      <div className="page-heading page-heading--split">
        <div>
          <p className="eyebrow">Access governance</p>
          <h1>Review access requests</h1>
          <p>Review employee and contractor access requests and create governed status-change proposals.</p>
        </div>
        <div className="role-callout">
          <span>Viewing as</span>
          <strong>{persona}</strong>
          <small>Backend permissions remain authoritative.</small>
        </div>
      </div>

      <div className="access-toolbar">
        <label>
          <Filter aria-hidden="true" />
          <span>Status</span>
          <select value={filter} onChange={(event) => {
            setLoading(true);
            setError(null);
            setFilter(event.target.value as ApprovalStatus | "all");
          }}>
            <option value="all">All requests</option>
            <option value="pending">Pending</option>
            <option value="approved">Approved</option>
            <option value="denied">Denied</option>
          </select>
        </label>
        <button className="button button--quiet" type="button" onClick={() => {
          setLoading(true);
          setError(null);
          setRefreshKey((value) => value + 1);
        }}>
          <RefreshCw aria-hidden="true" /> Refresh
        </button>
      </div>

      {loading ? <LoadingState label="Loading access requests…" /> : null}
      {error ? <ErrorState title="We couldn't load access requests" message={error} /> : null}
      {!loading && !error && records.length === 0 ? (
        <EmptyState title="No requests match this view" message="Choose another status filter to continue." />
      ) : null}

      {!loading && !error && records.length ? (
        <div className="access-layout">
          <section className="request-list" aria-label="Access requests">
            <div className="request-list__header">
              <span>{records.length} access request{records.length === 1 ? "" : "s"}</span>
              <span>Select a request to review</span>
            </div>
            {records.map((record) => (
              <button
                type="button"
                key={record.request_id}
                className={`request-row ${selectedId === record.request_id ? "request-row--selected" : ""}`}
                onClick={() => {
                  setSelectedId(record.request_id);
                  setProposalError(null);
                }}
                aria-pressed={selectedId === record.request_id}
              >
                <span className="request-row__identity"><strong>{record.request_id}</strong><small>{record.system_name}</small></span>
                <span className="request-row__role">{record.requested_role}</span>
                <StatusBadge label={record.approval_status} tone={record.approval_status} />
                <ChevronRight aria-hidden="true" />
              </button>
            ))}
          </section>

          {selected ? (
            <aside className="request-detail" aria-label={`Details for ${selected.request_id}`}>
              <div className="request-detail__heading">
                <div><p className="eyebrow">Selected request</p><h2>{selected.request_id}</h2></div>
                <StatusBadge label={selected.approval_status} tone={selected.approval_status} />
              </div>
              <dl className="detail-grid">
                <div><dt>Identity</dt><dd><UserRound aria-hidden="true" />{selected.subject_id}</dd><small>{selected.subject_type}</small></div>
                <div><dt>Requested role</dt><dd>{selected.requested_role}</dd><small>{selected.system_name}</small></div>
                <div><dt>Access window</dt><dd><CalendarDays aria-hidden="true" />{formatDate(selected.requested_start_date)}</dd><small>to {formatDate(selected.requested_end_date)}</small></div>
              </dl>

              <div className="proposal-box">
                <div className="proposal-box__heading"><ShieldAlert aria-hidden="true" /><div><strong>Propose a status change</strong><span>This records intent. It does not execute the change.</span></div></div>
                {can("propose") ? (
                  <>
                    <label htmlFor="proposed-status">Proposed status</label>
                    <select id="proposed-status" value={effectiveProposalStatus} onChange={(event) => setProposalStatus(event.target.value as ApprovalStatus)}>
                      {allowedStatuses.map((status) => <option key={status} value={status}>{status}</option>)}
                    </select>
                    <button className="button button--primary button--full" type="button" disabled={proposalBusy} onClick={createProposal}>
                      {proposalBusy ? "Creating proposal…" : "Create proposal"}<ArrowRight aria-hidden="true" />
                    </button>
                  </>
                ) : (
                  <div className="permission-note">Proposal creation requires the Operator role.</div>
                )}
                {proposalError ? <p className="form-error" role="alert">{proposalError}</p> : null}
                {pending?.accessRequest.request_id === selected.request_id ? (
                  <div className="proposal-created" role="status">
                    <strong>Proposal created</strong>
                    <p>No access change has been executed yet. This proposal requires human approval.</p>
                    <span>{selected.approval_status} → {pending.proposedStatus}</span>
                    <small>Approval ID: {pending.response.approval_id}</small>
                    <button type="button" onClick={() => navigate("/approvals")}>Review approval <ArrowRight aria-hidden="true" /></button>
                  </div>
                ) : null}
              </div>
            </aside>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
