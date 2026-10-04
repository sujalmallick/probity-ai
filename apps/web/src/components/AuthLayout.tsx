import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

/** Centred auth screen (sign in, sign up, password help). Always dark, like the landing page. */
export function AuthLayout({ children, title, subtitle }: { children: ReactNode; title: ReactNode; subtitle?: ReactNode }) {
  return (
    <div className="landing relative flex min-h-screen flex-col overflow-hidden">
      <div aria-hidden className="pointer-events-none absolute inset-0">
        <div className="absolute top-[-25%] left-1/2 h-[640px] w-[900px] -translate-x-1/2 rounded-full bg-white/[0.07] blur-[140px]" />
        <div
          className="absolute inset-0 opacity-[0.35]"
          style={{
            backgroundImage: "linear-gradient(rgba(255,255,255,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.05) 1px, transparent 1px)",
            backgroundSize: "64px 64px",
            maskImage: "radial-gradient(ellipse 60% 50% at 50% 30%, black, transparent)",
            WebkitMaskImage: "radial-gradient(ellipse 60% 50% at 50% 30%, black, transparent)",
          }}
        />
      </div>

      <header className="relative z-10 flex items-center justify-between px-6 py-5 sm:px-10">
        <Link to="/" className="flex items-center gap-2.5 rounded-md" aria-label="Probity home">
          <img src="/landing/logo.svg" alt="" className="h-7 w-7" />
          <span className="text-lg font-bold tracking-tight">Probity</span>
        </Link>
        <Link to="/" className="flex items-center gap-1.5 rounded-md text-sm text-muted-foreground transition-colors duration-200 hover:text-foreground">
          <ArrowLeft size={14} aria-hidden />Back to home
        </Link>
      </header>

      <main className="relative z-10 flex flex-1 items-center justify-center px-4 pt-4 pb-16">
        <div className="w-full max-w-[420px]">
          <div className="rounded-3xl border border-border bg-card/80 p-7 shadow-[0_30px_80px_-20px_rgba(0,0,0,0.8)] backdrop-blur-xl sm:p-9">
            <div className="mb-7 flex flex-col items-center text-center">
              <img src="/landing/logo.svg" alt="" className="mb-5 h-11 w-11" />
              <h1 className="text-[28px] leading-tight font-medium tracking-[-0.8px]">{title}</h1>
              {subtitle && <p className="mt-1.5 text-[15px] text-muted-foreground">{subtitle}</p>}
            </div>
            {children}
          </div>
          <p className="mt-6 text-center text-xs text-muted-foreground">Probity never moves money. Every payment decision stays with a person.</p>
        </div>
      </main>
    </div>
  );
}

/** Shared input styling for auth forms on the dark auth screen. */
export const authInput =
  "h-12 w-full rounded-xl border bg-black/40 pr-4 pl-11 text-[15px] text-foreground transition-colors duration-200 placeholder:text-muted-foreground/50 focus:outline-2 focus:outline-offset-2 focus:outline-foreground";
export const authPrimary =
  "flex h-12 w-full cursor-pointer items-center justify-center gap-2 rounded-xl bg-foreground text-[15px] font-semibold text-background transition-opacity duration-200 hover:opacity-90 disabled:cursor-wait disabled:opacity-70";
