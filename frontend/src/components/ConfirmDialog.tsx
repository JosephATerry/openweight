import { useEffect, useRef } from "react";
import { ShieldCheck, X } from "lucide-react";

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  tone = "primary",
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  tone?: "primary" | "danger";
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={dialogRef}
      className="confirm-dialog"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
    >
      <button
        className="icon-button confirm-dialog__close"
        type="button"
        aria-label="Close confirmation"
        disabled={busy}
        onClick={onCancel}
      >
        <X aria-hidden="true" />
      </button>
      <span className="confirm-dialog__icon">
        <ShieldCheck aria-hidden="true" />
      </span>
      <p className="eyebrow">Human authorization required</p>
      <h2>{title}</h2>
      <p>{description}</p>
      <div className="confirm-dialog__actions">
        <button className="button button--quiet" type="button" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
        <button
          className={`button ${tone === "danger" ? "button--danger" : "button--primary"}`}
          type="button"
          disabled={busy}
          onClick={onConfirm}
        >
          {busy ? "Submitting decision…" : confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
