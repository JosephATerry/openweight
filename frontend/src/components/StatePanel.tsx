import type { ReactNode } from "react";
import { AlertTriangle, Inbox, LoaderCircle } from "lucide-react";

export function LoadingState({ label = "Loading current state" }: { label?: string }) {
  return (
    <div className="state-panel" role="status" aria-live="polite">
      <LoaderCircle className="state-panel__spinner" aria-hidden="true" />
      <div>
        <strong>{label}</strong>
        <p>Securely contacting the OpenWeight service.</p>
      </div>
    </div>
  );
}

export function EmptyState({ title, message }: { title: string; message: string }) {
  return (
    <div className="state-panel">
      <Inbox aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <p>{message}</p>
      </div>
    </div>
  );
}

export function ErrorState({
  title = "We couldn't load this view",
  message,
  action,
}: {
  title?: string;
  message: string;
  action?: ReactNode;
}) {
  return (
    <div className="state-panel state-panel--error" role="alert">
      <AlertTriangle aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <p>{message}</p>
        {action ? <div className="state-panel__action">{action}</div> : null}
      </div>
    </div>
  );
}
