import { createContext, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

import type {
  AccessRequestRecord,
  ApprovalProposalResponse,
  ApprovalResumeResponse,
  ApprovalStatus,
} from "../api/types";

export interface PendingApproval {
  accessRequest: AccessRequestRecord;
  proposedStatus: ApprovalStatus;
  response: ApprovalProposalResponse;
}

interface ApprovalContextValue {
  pending: PendingApproval | null;
  outcome: ApprovalResumeResponse | null;
  setPending: (pending: PendingApproval) => void;
  setOutcome: (outcome: ApprovalResumeResponse) => void;
  reset: () => void;
}

const ApprovalContext = createContext<ApprovalContextValue | null>(null);

export function ApprovalProvider({ children }: { children: ReactNode }) {
  const [pending, updatePending] = useState<PendingApproval | null>(null);
  const [outcome, updateOutcome] = useState<ApprovalResumeResponse | null>(null);
  const value = useMemo<ApprovalContextValue>(
    () => ({
      pending,
      outcome,
      setPending: (next) => {
        updatePending(next);
        updateOutcome(null);
      },
      setOutcome: updateOutcome,
      reset: () => {
        updatePending(null);
        updateOutcome(null);
      },
    }),
    [outcome, pending],
  );

  return <ApprovalContext.Provider value={value}>{children}</ApprovalContext.Provider>;
}

export function useApproval(): ApprovalContextValue {
  const value = useContext(ApprovalContext);
  if (!value) throw new Error("useApproval must be used within its provider");
  return value;
}
