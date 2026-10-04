import { useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, BotOff, CheckCircle2, ChevronDown, CircleDashed, Loader2, SearchX, ShieldAlert, ShieldCheck, X, XCircle } from "lucide-react";
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={onClose}>
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

/** A section that failed to load: the server's message (with its reference) and a way to try again. */
export function LoadError({ error, onRetry, className = "" }: { error: string; onRetry?: () => void; className?: string }) {
  return (
    <div role="alert" className={`card flex flex-wrap items-center gap-3 p-4 text-sm ${className}`}>
      <AlertTriangle size={16} className="shrink-0 text-high" aria-hidden />
      <span className="min-w-0 flex-1 break-words">{error}</span>
      {onRetry && <button className="btn !py-1 text-xs" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`pulse rounded-md bg-surface-2 ${className}`} />;
}

/** Right-hand slide-over panel. Escape or the backdrop closes it; focus moves into it on open. */
export function Drawer({ title, onClose, children }: { title: ReactNode; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    ref.current?.querySelector<HTMLElement>("button")?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal aria-label={typeof title === "string" ? title : "Panel"}>
      <div className="fade-in absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      <div ref={ref} className="drawer-in absolute inset-y-0 right-0 flex w-[420px] max-w-[92vw] flex-col border-l border-line bg-bg shadow-2xl">
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <h2 className="text-base font-semibold">{title}</h2>
          <button className="rounded p-1 text-muted hover:bg-surface-2" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-4">{children}</div>
      </div>
    </div>
  );
}

/** Small dropdown menu: Escape or a click outside closes it, arrow keys move between items. */
export function MenuButton({ label, icon, items, align = "right", up = false }: { label: ReactNode; icon?: ReactNode; items: { label: string; hint?: string; icon?: ReactNode; onSelect: () => void }[]; align?: "left" | "right"; up?: boolean }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const first = wrap.current?.querySelector<HTMLElement>('[role="menuitem"]');
    first?.focus();
    const onDown = (e: MouseEvent) => !wrap.current?.contains(e.target as Node) && setOpen(false);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setOpen(false); btn.current?.focus(); }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        const els = [...(wrap.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
        const i = els.indexOf(document.activeElement as HTMLElement);
        els[(i + (e.key === "ArrowDown" ? 1 : -1) + els.length) % els.length]?.focus();
        e.preventDefault();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);
  return (
    <div ref={wrap} className="relative">
      <button ref={btn} className="btn !py-1.5 text-sm" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        {icon}{label}{!up && <ChevronDown size={14} className={`text-muted transition-transform duration-150 ${open ? "rotate-180" : ""}`} aria-hidden />}
      </button>
      {open && (
        <div role="menu" className={`fade-in absolute z-40 min-w-[200px] ${up ? "bottom-full mb-1.5" : "top-full mt-1.5"} rounded-xl border border-line bg-surface p-1 shadow-xl ${align === "right" ? "right-0" : "left-0"}`}>
          {items.map((it) => (
            <button
              key={it.label}
              role="menuitem"
              className="flex w-full items-start gap-2.5 rounded-lg px-3 py-2 text-left text-sm hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none"
              onClick={() => { setOpen(false); it.onSelect(); }}
            >
              {it.icon && <span className="mt-0.5 text-muted">{it.icon}</span>}
              <span><span className="block">{it.label}</span>{it.hint && <span className="block text-xs text-muted">{it.hint}</span>}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export type Fallback = { kind?: string; label?: string; reason?: string } | null | undefined;
const FALLBACK_LABEL = "rule-based fallback, AI unavailable";

/** Shown next to any result the server produced with rules because the AI was unavailable. Tooltip = why. */
export function FallbackBadge({ fb, className = "" }: { fb: Fallback; className?: string }) {
  if (!fb) return null;
  const label = fb.label || FALLBACK_LABEL;
  const text = fb.reason ? `${label}: ${fb.reason}` : label;
  return (
    <span
      tabIndex={0}
      title={fb.reason || label}
      aria-label={text}
      className={`inline-flex items-center gap-1 rounded-md border border-dashed border-medium/50 bg-medium-soft px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap text-medium align-middle ${className}`}
    >
      <BotOff size={11} aria-hidden />{label}
    </span>
  );
}

/** A check or claim that could not be verified because a tool or data source failed. Never shown as a pass. */
export function CouldNotVerify({ reason, className = "" }: { reason?: string | null; className?: string }) {
  return (
    <span className={`inline-flex items-start gap-1 text-xs font-medium text-medium ${className}`}>
      <SearchX size={13} className="mt-px shrink-0" aria-hidden />
      <span>Could not verify{reason ? `: ${reason}` : ""}</span>
    </span>
  );
}
