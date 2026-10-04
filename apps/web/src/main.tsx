import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { ClerkProvider, SignedIn, SignedOut, SignIn, UserButton, useAuth as useClerkAuth, useClerk } from "@clerk/clerk-react";
import { BarChart3, Brain, Building2, FilePlus2, Gauge as GaugeIcon, LogOut, Moon, Settings, Sun, Users } from "lucide-react";
import "./index.css";
import { AuthCtx, useAuth } from "./lib/auth";
import { api, getLocalSession, setLocalSession, setTokenGetter, setUnauthorizedHandler, type Me } from "./lib/api";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import NewCase from "./pages/NewCase";
import CaseView from "./pages/CaseView";
import { VendorDetail, Vendors } from "./pages/Vendors";
import Memory from "./pages/Memory";
import Benchmark from "./pages/Benchmark";
import SettingsPage from "./pages/Settings";
import Team from "./pages/Team";
import { Spinner } from "./components/ui";

const CLERK_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined;

const ROLE_HINT: Record<string, string> = {
  viewer: "Read only",
  accountant: "Upload · annotate",
  approver: "Approve · reject · verify",
  owner: "Policy · team",
};

function Brand({ size = 8 }: { size?: number }) {
  return (
    <div className="flex items-center gap-2">
      <div className="flex items-center justify-center rounded-lg bg-accent text-sm font-bold text-white" style={{ width: size * 4, height: size * 4 }}>P</div>
      <div>
        <div className="text-[15px] font-bold leading-tight">Probity</div>
        <div className="text-[11px] leading-tight text-muted">Evidence before payment.</div>
      </div>
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const { user, mode, signOut } = useAuth();
  const [ready, setReady] = useState<{ tools_mode: string; llm_mode: string } | null>(null);
  const [theme, setTheme] = useState<string | null>(() => {
    try {
      return localStorage.getItem("probity.theme");
    } catch {
      return null;
    }
  });
  useEffect(() => {
    api("/ready").then(setReady).catch(() => setReady(null));
  }, []);
  useEffect(() => {
    if (theme) document.documentElement.dataset.theme = theme;
    else delete document.documentElement.dataset.theme;
    try {
      if (theme) localStorage.setItem("probity.theme", theme);
      else localStorage.removeItem("probity.theme");
    } catch {
      /* ignore */
    }
  }, [theme]);
  const dark = theme === "dark" || (!theme && window.matchMedia?.("(prefers-color-scheme: dark)").matches);
  const link = ({ isActive }: { isActive: boolean }) =>
    `flex shrink-0 items-center gap-2.5 whitespace-nowrap rounded-lg px-3 py-2 text-sm ${isActive ? "bg-accent-soft font-semibold text-accent" : "text-muted hover:bg-surface-2 hover:text-ink"}`;
  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <aside className="flex shrink-0 flex-col gap-1 border-b border-line bg-surface p-3 md:sticky md:top-0 md:h-screen md:w-56 md:border-r md:border-b-0">
        <div className="mb-3 flex items-center justify-between px-2 pt-1">
          <Brand />
          <div className="flex items-center gap-1 md:hidden">
            {mode === "clerk" && <UserButton />}
            <button className="btn !px-2 !py-1" aria-label="Toggle theme" onClick={() => setTheme(dark ? "light" : "dark")}>{dark ? <Sun size={13} /> : <Moon size={13} />}</button>
          </div>
        </div>
        <nav className="flex gap-1 overflow-x-auto pb-1 md:flex-col md:pb-0">
          <NavLink to="/dashboard" className={link}><GaugeIcon size={16} />Dashboard</NavLink>
          <NavLink to="/cases/new" className={link}><FilePlus2 size={16} />New case</NavLink>
          <NavLink to="/vendors" className={link}><Building2 size={16} />Vendors</NavLink>
          <NavLink to="/memory" className={link}><Brain size={16} />Case memory</NavLink>
          <NavLink to="/benchmark" className={link}><BarChart3 size={16} />Benchmark</NavLink>
          <NavLink to="/settings/policy" className={link}><Settings size={16} />Policy</NavLink>
          <NavLink to="/settings/team" className={link}><Users size={16} />Team</NavLink>
        </nav>
        <div className="mt-auto hidden flex-col gap-2 md:flex">
          {ready && (ready.tools_mode !== "live" || ready.llm_mode !== "live") && (
            <div className="rounded-lg bg-medium-soft px-3 py-2 text-[11px] text-medium" title="Recorded tool responses and/or deterministic agents are in use">
              <b>Offline mode</b> · tools {ready.tools_mode} · AI {ready.llm_mode}
            </div>
          )}
          {user && (
            <div className="rounded-lg border border-line px-3 py-2">
              <div className="flex items-center gap-2">
                {mode === "clerk" && <UserButton />}
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold">{user.name}</div>
                  <div className="truncate text-[11px] text-muted">{user.workspace?.name}</div>
                </div>
              </div>
              <div className="mt-1 text-[11px] text-muted">{user.role} · {ROLE_HINT[user.role]}</div>
              <div className="mt-2 flex gap-1">
                <button className="btn flex-1 !px-2 !py-1 text-xs" onClick={signOut}><LogOut size={13} />{mode === "clerk" ? "Sign out" : "Switch user"}</button>
                <button className="btn !px-2 !py-1" aria-label="Toggle theme" onClick={() => setTheme(dark ? "light" : "dark")}>{dark ? <Sun size={13} /> : <Moon size={13} />}</button>
              </div>
            </div>
          )}
        </div>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-5 md:px-8">{children}</main>
    </div>
  );
}

function AppRoutes() {
  return (
    <Shell>
      <Routes>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/cases/new" element={<NewCase />} />
        <Route path="/cases/:id" element={<CaseView />} />
        <Route path="/vendors" element={<Vendors />} />
        <Route path="/vendors/:id" element={<VendorDetail />} />
        <Route path="/memory" element={<Memory />} />
        <Route path="/benchmark" element={<Benchmark />} />
        <Route path="/settings/policy" element={<SettingsPage />} />
        <Route path="/settings/team" element={<Team />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </Shell>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex min-h-screen items-center justify-center p-4">{children}</div>;
}

// ---------------------------------------------------------------- Clerk (production)

function ClerkApp() {
  const { isLoaded, isSignedIn, getToken } = useClerkAuth();
  const clerk = useClerk();
  const [user, setUser] = useState<Me | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setTokenGetter(() => getToken());
    return () => setTokenGetter(null);
  }, [getToken]);
  useEffect(() => {
    if (isLoaded && isSignedIn) api<Me>("/me").then(setUser).catch((e) => setErr(e.message));
    if (isLoaded && !isSignedIn) setUser(null);
  }, [isLoaded, isSignedIn]);
  const signOut = () => clerk.signOut({ redirectUrl: "/" });
  return (
    <AuthCtx.Provider value={{ user, mode: "clerk", setUser, signOut }}>
      <SignedOut>
        <Centered>
          <div className="flex flex-col items-center gap-6">
            <Brand size={10} />
            <SignIn routing="hash" />
            <p className="text-xs text-muted">The AI investigates. Code scores. A human decides.</p>
          </div>
        </Centered>
      </SignedOut>
      <SignedIn>
        {user ? <AppRoutes /> : <Centered>{err ? <div className="card p-4 text-sm text-high">Could not load your account: {err}</div> : <Spinner size={20} />}</Centered>}
      </SignedIn>
    </AuthCtx.Provider>
  );
}

// ---------------------------------------------------------------- local demo auth (dev only)

function LocalApp() {
  const [user, setUser] = useState<Me | null>(() => getLocalSession()?.user ?? null);
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setLocalSession(null);
      setUser(null);
    });
    return () => setUnauthorizedHandler(null);
  }, []);
  const signOut = () => {
    setLocalSession(null);
    setUser(null);
  };
  return (
    <AuthCtx.Provider value={{ user, mode: "local", setUser, signOut }}>
      <Routes>
        <Route path="/login" element={user ? <Navigate to="/dashboard" replace /> : <Login />} />
        <Route path="*" element={user ? <AppRoutes /> : <Navigate to="/login" replace />} />
      </Routes>
    </AuthCtx.Provider>
  );
}

function Root() {
  const [mode, setMode] = useState<"local" | "clerk" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    fetch("/api/v1/auth/config").then((r) => r.json()).then((c) => setMode(c.mode)).catch(() => setErr("The Probity API is unreachable."));
  }, []);
  if (err) return <Centered><div className="card p-4 text-sm text-high">{err}</div></Centered>;
  if (!mode) return <Centered><Spinner size={20} /></Centered>;
  if (mode === "clerk") {
    if (!CLERK_KEY) return <Centered><div className="card max-w-md p-4 text-sm text-high">The API uses Clerk, but this web build has no VITE_CLERK_PUBLISHABLE_KEY.</div></Centered>;
    return (
      <ClerkProvider publishableKey={CLERK_KEY} afterSignOutUrl="/">
        <BrowserRouter><ClerkApp /></BrowserRouter>
      </ClerkProvider>
    );
  }
  return <BrowserRouter><LocalApp /></BrowserRouter>;
}

ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><Root /></React.StrictMode>);
