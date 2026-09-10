import type { ApprovalStatus } from "../api/types";

type Tone = ApprovalStatus | "ready" | "unavailable" | "disabled" | "neutral";

export function StatusBadge({
  label,
  tone = "neutral",
}: {
  label: string;
  tone?: Tone;
}) {
  return (
    <span className={`status-badge status-badge--${tone}`}>
      <span className="status-badge__dot" aria-hidden="true" />
      {label}
    </span>
  );
}
