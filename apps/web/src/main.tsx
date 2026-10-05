import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { ClerkProvider, SignedIn, SignedOut, UserButton, useAuth as useClerkAuth, useClerk } from "@clerk/clerk-react";
import { BookOpen, Brain, Building2, ClipboardCheck, FilePlus2, Gauge as GaugeIcon, LogOut, Menu, Moon, PanelLeftClose, PanelLeftOpen, Settings, Sun, Users, WifiOff, X } from "lucide-react";
import "./index.css";
import { AuthCtx, useAuth } from "./lib/auth";
import { api, ApiError, can, errMsg, setTokenGetter, setUnauthorizedHandler, type Me } from "./lib/api";
import { InvitationChoice, type Invitation } from "./components/InvitationChoice";
import Login, { SignInUnavailable, SignUpPage } from "./pages/Login";
import Landing from "./pages/Landing";
import Guide from "./pages/Guide";
import Dashboard from "./pages/Dashboard";
import NewCase from "./pages/NewCase";
import CaseView from "./pages/CaseView";
import { VendorDetail, Vendors } from "./pages/Vendors";
import ImportPage from "./pages/Import";
import Memory from "./pages/Memory";
import SettingsPage from "./pages/Settings";
import Team from "./pages/Team";
import StatusPage, { fetchReady } from "./pages/Status";
import Baseline from "./pages/Baseline";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Spinner } from "./components/ui";
import { integrationsNeedingAction, loadAppConfig, useAppConfig } from "./lib/config";
import { LogoMark } from "./components/Logo";

const CLERK_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined;

const ROLE_HINT: Record<string, string> = {
  viewer: "Read only",
  accountant: "Upload · annotate",
  approver: "Approve · reject · verify",
  owner: "Policy · team",
};

function Brand({ size = 8, tagline = false }: { size?: number; tagline?: boolean }) {
  return (
    <Link to="/" className="flex items-center gap-2.5 rounded-lg" aria-label="Probity home page">
      <LogoMark size={size * 4} />
      <div>
        <div className="text-[15px] leading-tight font-bold tracking-tight">Probity</div>
        {tagline && <div className="text-[11px] leading-tight text-muted">Evidence before payment.</div>}
      </div>
    </Link>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const { user, signOut } = useAuth();
  const cfg = useAppConfig();
  const location = useLocation();
  const [theme, setTheme] = useState<string | null>(() => readPref("probity.theme"));
  const [collapsed, setCollapsed] = useState(() => readPref("probity.sidebar") === "collapsed");
  const [mobileOpen, setMobileOpen] = useState(false);
  useEffect(() => {
    if (theme) document.documentElement.dataset.theme = theme;
    else delete document.documentElement.dataset.theme;
    writePref("probity.theme", theme);
  }, [theme]);
  useEffect(() => writePref("probity.sidebar", collapsed ? "collapsed" : null), [collapsed]);
  useEffect(() => setMobileOpen(false), [location.pathname]);
  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMobileOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileOpen]);
  const dark = theme === "dark" || (!theme && window.matchMedia?.("(prefers-color-scheme: dark)").matches);
  const toggleTheme = () => setTheme(dark ? "light" : "dark");

  const nav = [
    { to: "/dashboard", icon: GaugeIcon, label: "Dashboard" },
    { to: "/cases/new", icon: FilePlus2, label: "New case" },
    { to: "/vendors", icon: Building2, label: "Vendors" },
    ...(can(user?.role, "approver") ? [{ to: "/baseline", icon: ClipboardCheck, label: "Approvals" }] : []),
    { to: "/memory", icon: Brain, label: "Case memory" },
    { to: "/settings/policy", icon: Settings, label: "Policy" },
    { to: "/settings/team", icon: Users, label: "Team" },
    { to: "/guide", icon: BookOpen, label: "How to use" },
  ];
  const navList = (mini: boolean) => (
    <nav className="flex flex-col gap-1" aria-label="App">
      {nav.map(({ to, icon: Icon, label }) => (
        <NavLink
          key={to}
          to={to}
          title={mini ? label : undefined}
          className={({ isActive }) =>
            `flex items-center gap-2.5 whitespace-nowrap rounded-lg py-2 text-sm transition-colors duration-150 ${mini ? "justify-center px-0" : "px-3"} ${
              isActive ? "bg-surface-2 font-medium text-ink shadow-[inset_0_0_0_1px_var(--border)]" : "text-muted hover:bg-surface-2 hover:text-ink"
            }`
          }
        >
          <Icon size={17} aria-hidden />
          <span className={mini ? "sr-only" : ""}>{label}</span>
        </NavLink>
      ))}
    </nav>
  );
  const iconBtn = "flex h-8 w-8 items-center justify-center rounded-lg text-muted transition-colors duration-150 hover:bg-surface-2 hover:text-ink";
  const userCard = user && (
    <div className="card px-3 py-3">
      <div className="flex items-center gap-2">
        <UserButton />
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{user.name}</div>
          <div className="truncate text-[11px] text-muted">{user.workspace?.name}</div>
        </div>
      </div>
      <div className="mt-1 text-[11px] text-muted">{user.role} · {ROLE_HINT[user.role]}</div>
      <div className="mt-2 flex gap-1">
        <button className="btn flex-1 !px-2 !py-1 text-xs" onClick={signOut}><LogOut size={13} />Sign out</button>
        <button className="btn !px-2 !py-1" aria-label={dark ? "Use light theme" : "Use dark theme"} onClick={toggleTheme}>{dark ? <Sun size={13} /> : <Moon size={13} />}</button>
      </div>
    </div>
  );

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      {/* Mobile top bar */}
      <header className="sticky top-0 z-40 flex items-center justify-between border-b border-line bg-bg/90 px-3 py-2.5 backdrop-blur md:hidden">
        <div className="flex items-center gap-1.5">
          <button className={iconBtn} aria-label="Open menu" aria-expanded={mobileOpen} aria-controls="mobile-nav" onClick={() => setMobileOpen(true)}><Menu size={19} /></button>
          <Brand />
        </div>
        <div className="flex items-center gap-1">
          <LiveStatus compact />
          <UserButton />
          <button className={iconBtn} aria-label={dark ? "Use light theme" : "Use dark theme"} onClick={toggleTheme}>{dark ? <Sun size={15} /> : <Moon size={15} />}</button>
        </div>
      </header>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-modal aria-label="Menu">
          <div className="fade-in absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setMobileOpen(false)} />
          <div id="mobile-nav" className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col gap-4 border-r border-line bg-bg p-4">
            <div className="flex items-center justify-between">
              <Brand tagline />
              <button className={iconBtn} aria-label="Close menu" onClick={() => setMobileOpen(false)} autoFocus><X size={18} /></button>
            </div>
            {navList(false)}
            <div className="mt-auto flex flex-col gap-2"><LiveStatus />{userCard}</div>
          </div>
        </div>
      )}

      {/* Desktop sidebar */}
      <aside
        className={`hidden shrink-0 flex-col gap-1 border-r border-line bg-bg py-5 transition-[width] duration-200 md:sticky md:top-0 md:flex md:h-screen ${collapsed ? "w-[72px] px-3" : "w-60 px-4"}`}
        aria-label="Sidebar"
      >
        <div className={`mb-4 flex items-center pt-1 ${collapsed ? "flex-col gap-3" : "justify-between px-1"}`}>
          {collapsed ? <Link to="/" aria-label="Probity home page" className="rounded-lg"><LogoMark size={32} /></Link> : <Brand />}
          <button className={iconBtn} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} title={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-expanded={!collapsed} onClick={() => setCollapsed(!collapsed)}>
            {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
          </button>
        </div>
        {navList(collapsed)}
        <div className="mt-auto flex flex-col gap-2">
          {collapsed ? (
            <>
              <div className="flex justify-center"><LiveStatus compact /></div>
              <button className={`${iconBtn} mx-auto`} aria-label={dark ? "Use light theme" : "Use dark theme"} title="Theme" onClick={toggleTheme}>{dark ? <Sun size={15} /> : <Moon size={15} />}</button>
              {user && (
                <button className={`${iconBtn} mx-auto`} aria-label="Sign out" title={`${user.name} · Sign out`} onClick={signOut}>
                  <LogOut size={15} />
                </button>
              )}
            </>
          ) : (
            <><LiveStatus />{userCard}</>
          )}
        </div>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-6 md:px-10 md:py-9">
        <DegradedBanner />
        <ErrorBoundary resetKey={location.pathname}>{children}</ErrorBoundary>
      </main>
    </div>
  );
}

/** "Live" status from /app/config.integrations; integrations that aren't available are listed in the tooltip. */
function LiveStatus({ compact = false }: { compact?: boolean }) {
  const cfg = useAppConfig();
  const missing = integrationsNeedingAction(cfg);
  const detail = missing.length ? `Live · needs setting up: ${missing.join(", ")}` : "Live · everything needed is connected";
  return (
    <Link
      to="/status"
      title={`${detail} · open system status`}
      aria-label={`${detail}. Open system status`}
      className={`inline-flex items-center gap-2 rounded-lg text-xs text-muted outline-offset-2 transition-colors duration-150 hover:bg-surface-2 hover:text-ink ${compact ? "p-1.5" : "px-3 py-1.5"}`}
    >
      <span className="relative flex h-2 w-2" aria-hidden>
        <span className="pulse absolute inline-flex h-full w-full rounded-full bg-low opacity-60" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-low" />
      </span>
      {!compact && <span>Live{missing.length ? <span className="text-muted"> · {missing.length} to set up</span> : null}</span>}
    </Link>
  );
}

/** Shown above every app page while the server reports a problem (/ready not ok) or can't be reached. Checked every minute. */
function DegradedBanner() {
  const [state, setState] = useState<"ok" | "degraded" | "unreachable">("ok");
  useEffect(() => {
    let alive = true;
    const check = () => fetchReady().then((r) => alive && setState(r === null ? "unreachable" : r.ok ? "ok" : "degraded"));
    check();
    const t = setInterval(check, 60_000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  if (state === "ok") return null;
  return (
    <div role="status" className="mx-auto mb-5 flex max-w-6xl items-start gap-2.5 rounded-lg border border-medium/40 bg-medium-soft px-3.5 py-2.5 text-sm text-medium">
      <WifiOff size={16} className="mt-0.5 shrink-0" aria-hidden />
      <span className="min-w-0 flex-1">
        {state === "unreachable"
          ? "Can't reach Probity's server right now. It may be starting up; anything you change may not save until it's back."
          : "Part of Probity isn't working right now, so investigations may be delayed or fail."}{" "}
        <Link to="/status" className="font-medium underline underline-offset-2">System status</Link>
      </span>
    </div>
  );
}

function readPref(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function writePref(key: string, value: string | null) {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    /* private mode: preference lasts for this page only */
  }
}

function AppRoutes() {
  return (
    <Shell>
      <Routes>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/cases/new" element={<NewCase />} />
        <Route path="/cases/:id" element={<CaseView />} />
        <Route path="/vendors" element={<Vendors />} />
        <Route path="/vendors/import" element={<ImportPage />} />
        <Route path="/vendors/:id" element={<VendorDetail />} />
        <Route path="/memory" element={<Memory />} />
        <Route path="/settings/policy" element={<SettingsPage />} />
        <Route path="/settings/team" element={<Team />} />
        <Route path="/baseline" element={<Baseline />} />
        <Route path="/status" element={<StatusPage />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </Shell>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex min-h-screen items-center justify-center p-4">{children}</div>;
}

// ---------------------------------------------------------------- app (Clerk sign-in)

function ClerkApp() {
  const { isLoaded, isSignedIn, getToken } = useClerkAuth();
  const clerk = useClerk();
  const [user, setUser] = useState<Me | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [invites, setInvites] = useState<Invitation[] | null>(null);
  useEffect(() => {
    setTokenGetter(() => getToken());
    return () => setTokenGetter(null);
  }, [getToken]);
  useEffect(() => {
    // The API said the session is no longer valid: sign out once and land on sign-in with an explanation.
    let fired = false;
    setUnauthorizedHandler(() => {
      if (fired) return;
      fired = true;
      clerk.signOut({ redirectUrl: "/login?expired=1" });
    });
    return () => setUnauthorizedHandler(null);
  }, [clerk]);
  // A first-time user with pending invitations gets 409 `invitation_pending` from /me and must choose: accept one or
  // start their own workspace. Nobody is added to someone else's workspace without saying yes.
  const loadMe = () => {
    setErr(null);
    api<Me>("/me")
      .then((me) => { setUser(me); setInvites(null); })
      .catch((e) => {
        if (e instanceof ApiError && e.code === "invitation_pending") setInvites((e.details?.invitations as Invitation[]) ?? []);
        else setErr(errMsg(e));
      });
  };
  useEffect(() => {
    if (isLoaded && isSignedIn) loadMe();
    if (isLoaded && !isSignedIn) { setUser(null); setInvites(null); }
  }, [isLoaded, isSignedIn]);
  const cfg = useAppConfig();
  const signOut = () => clerk.signOut({ redirectUrl: "/" });
  return (
    <AuthCtx.Provider value={{ user, mode: "clerk", setUser, signOut }}>
      <SignedOut>
        <Routes>
          <Route path="/" element={cfg.features.landing_page ? <Landing /> : <Navigate to="/login" replace />} />
          <Route path="/login" element={<Login />} />
          <Route path="/guide" element={<Guide />} />
          <Route path="/status" element={<Centered><div className="w-full max-w-3xl"><StatusPage /></div></Centered>} />
          {cfg.auth.sign_up && <Route path="/sign-up" element={<SignUpPage />} />}
          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
      </SignedOut>
      <SignedIn>
        {user ? (
          <Routes>
            {cfg.features.landing_page && <Route path="/" element={<Landing signedIn />} />}
            <Route path="/guide" element={<Guide signedIn />} />
            <Route path="*" element={<AppRoutes />} />
          </Routes>
        ) : invites ? (
          <InvitationChoice invitations={invites} onJoined={(me) => { setUser(me); setInvites(null); }} onRefresh={loadMe} onSignOut={signOut} />
        ) : (
          <Centered>
            {err ? (
              <div className="card flex max-w-md flex-col gap-3 p-5 text-sm">
                <div className="font-semibold">Couldn't load your account</div>
                <p className="text-muted">{err}</p>
                <div className="flex gap-2"><button className="btn btn-primary" onClick={loadMe}>Try again</button><button className="btn" onClick={signOut}>Sign out</button></div>
              </div>
            ) : <Spinner size={20} />}
          </Centered>
        )}
      </SignedIn>
    </AuthCtx.Provider>
  );
}

function Root() {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    // Load /app/config once before rendering. If it fails, safe defaults are used and sign-in still works.
    loadAppConfig().then(() => setReady(true));
  }, []);
  if (!ready) return <Centered><Spinner size={20} /></Centered>;
  if (!CLERK_KEY) {
    // No sign-in provider in this build: the public pages still render, and the sign-in and sign-up pages explain why
    // nobody can sign in yet. Every app page leads to sign-in.
    return (
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/guide" element={<Guide />} />
          <Route path="/login" element={<SignInUnavailable mode="sign-in" />} />
          <Route path="/sign-up" element={<SignInUnavailable mode="sign-up" />} />
          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
      </BrowserRouter>
    );
  }
  return (
    <ClerkProvider publishableKey={CLERK_KEY} afterSignOutUrl="/" signInFallbackRedirectUrl="/dashboard" signUpFallbackRedirectUrl="/dashboard">
      <BrowserRouter><ClerkApp /></BrowserRouter>
    </ClerkProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><Root /></React.StrictMode>);
