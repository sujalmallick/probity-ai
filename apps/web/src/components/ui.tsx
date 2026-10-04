import { useEffect, useRef, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, CircleDashed, Loader2, ShieldAlert, ShieldCheck, X, XCircle } from "lucide-react";
import { STATUS_LABEL, tierColor, tierSoft } from "../lib/format";

export function TierChip({ tier, score }: { tier?: string; score?: number }) {
  if (!tier) return <span className="text-muted text-sm">—</span>;
  const Icon = tier === "LOW" ? ShieldCheck : tier === "MEDIUM" ? AlertTriangle : ShieldAlert;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-semibold whitespace-nowrap" style={{ color: tierColor[tier], background: tierSoft[tier] }}>
      <Icon size={13} aria-hidden />
      {score !== undefined && <span>{score}</span>}
      {tier}
    </span>
  );
}

export function StatusChip({ status }: { status: string }) {
  const tone =
    status === "AUTO_CLEARED" || status === "APPROVED" ? "var(--low)" :
    status === "AWAITING_HUMAN" ? "var(--medium)" :
    status === "REJECTED" || status === "FAILED" ? "var(--high)" :
    status === "AWAITING_VENDOR" ? "var(--accent)" : "var(--muted)";
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium whitespace-nowrap" style={{ color: tone }}>
      <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: tone }} aria-hidden />
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

export function VerifyBadge({ status }: { status: string }) {
  if (status === "verified")
    return <span className="inline-flex items-center gap-1 rounded-md bg-low-soft px-1.5 py-0.5 text-[11px] font-semibold text-low"><CheckCircle2 size={12} aria-hidden />Verified</span>;
  if (status === "refuted" || status === "dropped")
    return <span className="inline-flex items-center gap-1 rounded-md bg-info-soft px-1.5 py-0.5 text-[11px] font-semibold text-muted line-through"><XCircle size={12} aria-hidden />{status === "dropped" ? "Dropped" : "Refuted"}</span>;
  return <span className="inline-flex items-center gap-1 rounded-md bg-medium-soft px-1.5 py-0.5 text-[11px] font-semibold text-medium"><CircleDashed size={12} aria-hidden />Unconfirmed</span>;
}

export function Spinner({ size = 14 }: { size?: number }) {
  return <Loader2 size={size} className="spin" aria-label="Loading" />;
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    ref.current?.querySelector<HTMLElement>("textarea, input, select, button")?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div ref={ref} role="dialog" aria-modal aria-label={title} className={`card fade-in max-h-[90vh] w-full overflow-auto p-5 shadow-xl ${wide ? "max-w-2xl" : "max-w-lg"}`} onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold">{title}</h2>
          <button className="rounded p-1 text-muted hover:bg-surface-2" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
      <div className="text-muted">{icon}</div>
      <div className="font-semibold">{title}</div>
      {children}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`pulse rounded-md bg-surface-2 ${className}`} />;
}
