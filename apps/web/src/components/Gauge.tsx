import { useEffect, useRef, useState } from "react";
import { tierColor } from "../lib/format";

/** Animated 0–100 risk ring. Animates between scores (e.g. 70 → 20 after verification). */
export function Gauge({ score, tier, provisional, size = 148 }: { score: number | null; tier?: string; provisional?: boolean; size?: number }) {
  const [shown, setShown] = useState(score ?? 0);
  const from = useRef(score ?? 0);
  useEffect(() => {
    if (score === null) return;
    const start = performance.now();
    const a = from.current;
    const b = score;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const dur = reduce ? 0 : Math.min(1600, 300 + Math.abs(b - a) * 22);
    let raf = 0;
    const step = (t: number) => {
      const k = dur ? Math.min(1, (t - start) / dur) : 1;
      const e = 1 - Math.pow(1 - k, 3);
      setShown(Math.round(a + (b - a) * e));
      if (k < 1) raf = requestAnimationFrame(step);
      else from.current = b;
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [score]);

  const r = 42;
  const c = 2 * Math.PI * r;
  const arc = 0.75; // 270° gauge
  const shownTier = shown >= 80 ? "CRITICAL" : shown >= 60 ? "HIGH" : shown >= 30 ? "MEDIUM" : "LOW";
  const color = score === null ? "var(--border)" : tierColor[tier && shown === score ? tier : shownTier];
  return (
    <div className="relative" style={{ width: size, height: size }} role="img" aria-label={score === null ? "Risk score pending" : `Risk score ${score} of 100, ${tier}`}>
      <svg viewBox="0 0 100 100" width={size} height={size} style={{ transform: "rotate(135deg)" }}>
        <circle cx="50" cy="50" r={r} fill="none" stroke="var(--surface-2)" strokeWidth="9" strokeDasharray={`${c * arc} ${c}`} strokeLinecap="round" />
        <circle
          cx="50" cy="50" r={r} fill="none" stroke={color} strokeWidth="9" strokeLinecap="round"
          strokeDasharray={`${(c * arc * shown) / 100} ${c}`}
          style={{ transition: "stroke 0.4s" }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <div className="text-4xl font-semibold tracking-tight tabular-nums leading-none" style={{ color: score === null ? "var(--muted)" : color }}>{score === null ? "—" : shown}</div>
        <div className="mt-1 text-[11px] font-semibold tracking-wider text-muted">/ 100</div>
        {score !== null && <div className="mt-1 text-xs font-bold tracking-wide" style={{ color }}>{tier && shown === score ? tier : shownTier}</div>}
        {provisional && <div className="mt-0.5 text-[10px] text-muted">Provisional</div>}
      </div>
    </div>
  );
}
