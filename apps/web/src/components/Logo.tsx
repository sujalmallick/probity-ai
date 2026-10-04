/** Probity mark: a P whose bowl carries a check, evidence verified before payment. Inverts with the theme. */
export function LogoMark({ size = 28, className = "" }: { size?: number; className?: string }) {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} className={`shrink-0 ${className}`} aria-hidden>
      <rect width="32" height="32" rx="8" fill="var(--accent)" />
      <path d="M9 7.5h8.5a6.5 6.5 0 0 1 0 13H13V25a1.5 1.5 0 0 1-3 0V8.5a1 1 0 0 1 1-1z" fill="var(--accent-fg)" />
      <path d="M13.6 14.2l2.2 2.2 3.9-4.1" stroke="var(--accent)" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" fill="none" />
    </svg>
  );
}
