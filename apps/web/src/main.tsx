import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { ClerkProvider, SignedIn, SignedOut, SignIn, SignUp, UserButton, useAuth as useClerkAuth, useClerk } from "@clerk/clerk-react";
import { BarChart3, Brain, Building2, FilePlus2, Gauge as GaugeIcon, LogOut, Menu, Moon, PanelLeftClose, PanelLeftOpen, Settings, Sun, Users, WifiOff, X } from "lucide-react";
import "./index.css";
import { AuthCtx, useAuth } from "./lib/auth";
import { api, getLocalSession, setLocalSession, setTokenGetter, setUnauthorizedHandler, type Me } from "./lib/api";
import Login from "./pages/Login";
import { ForgotPasswordLocal, SignUpLocal } from "./pages/AuthHelp";
import Landing from "./pages/Landing";
import Dashboard from "./pages/Dashboard";
import NewCase from "./pages/NewCase";
import CaseView from "./pages/CaseView";
import { VendorDetail, Vendors } from "./pages/Vendors";
import ImportPage from "./pages/Import";
import Memory from "./pages/Memory";
import Benchmark from "./pages/Benchmark";
import SettingsPage from "./pages/Settings";
import Team from "./pages/Team";
import { Spinner } from "./components/ui";
import { isFallbackConfig, loadAppConfig, offlineIntegrations, useAppConfig } from "./lib/config";
import { LogoMark } from "./components/Logo";
import { AuthLayout } from "./components/AuthLayout";

const CLERK_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined;

// Clerk's form sits inside our own auth card, so drop its card chrome and header.
const CLERK_APPEARANCE = {
  variables: { colorPrimary: "#fafafa", colorTextOnPrimaryBackground: "#0a0a0a", colorBackground: "transparent", colorText: "#fafafa", colorTextSecondary: "#a6a6a6", colorInputBackground: "#0a0a0a", colorInputText: "#fafafa", borderRadius: "0.75rem" },
  elements: { rootBox: "w-full", cardBox: "w-full !shadow-none !border-0", card: "!bg-transparent !shadow-none !border-0 !p-0", header: "hidden", footer: "!bg-transparent" },
};

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
  const { user, mode, signOut } = useAuth();
  const cfg = useAppConfig();
  const location = useLocation();
  const offline = cfg.features.demo ? offlineIntegrations(cfg) : [];
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
    { to: "/memory", icon: Brain, label: "Case memory" },
    ...(cfg.features.benchmark ? [{ to: "/benchmark", icon: BarChart3, label: "Benchmark" }] : []),
    { to: "/settings/policy", icon: Settings, label: "Policy" },
    { to: "/settings/team", icon: Users, label: "Team" },
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
        {mode === "clerk" && <UserButton />}
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{user.name}</div>
          <div className="truncate text-[11px] text-muted">{user.workspace?.name}</div>
        </div>
      </div>
      <div className="mt-1 text-[11px] text-muted">{user.role} · {ROLE_HINT[user.role]}</div>
      <div className="mt-2 flex gap-1">
        <button className="btn flex-1 !px-2 !py-1 text-xs" onClick={signOut}><LogOut size={13} />{mode === "clerk" ? "Sign out" : "Switch user"}</button>
        <button className="btn !px-2 !py-1" aria-label={dark ? "Use light theme" : "Use dark theme"} onClick={toggleTheme}>{dark ? <Sun size={13} /> : <Moon size={13} />}</button>
      </div>
    </div>
  );
  const offlineBadge = offline.length > 0 && (
    <div className="rounded-lg bg-medium-soft px-3 py-2 text-[11px] text-medium" title="Recorded tool responses and/or deterministic agents are in use">
      <b>Offline mode</b> · {offline.join(", ")}
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
          {mode === "clerk" && <UserButton />}
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
            <div className="mt-auto flex flex-col gap-2">{offlineBadge}{userCard}</div>
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
              {offline.length > 0 && <div className="flex justify-center text-medium" title={`Offline mode · ${offline.join(", ")}`}><WifiOff size={16} aria-label="Offline mode" /></div>}
              <button className={`${iconBtn} mx-auto`} aria-label={dark ? "Use light theme" : "Use dark theme"} title="Theme" onClick={toggleTheme}>{dark ? <Sun size={15} /> : <Moon size={15} />}</button>
              {user && (
                <button className={`${iconBtn} mx-auto`} aria-label={mode === "clerk" ? "Sign out" : "Switch user"} title={`${user.name} · ${mode === "clerk" ? "Sign out" : "Switch user"}`} onClick={signOut}>
                  <LogOut size={15} />
                </button>
              )}
            </>
          ) : (
            <>{offlineBadge}{userCard}</>
          )}
        </div>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-6 md:px-10 md:py-9">{children}</main>
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
  const cfg = useAppConfig();
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
        {cfg.features.benchmark && <Route path="/benchmark" element={<Benchmark />} />}
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
  const cfg = useAppConfig();
  const signOut = () => clerk.signOut({ redirectUrl: "/" });
  return (
    <AuthCtx.Provider value={{ user, mode: "clerk", setUser, signOut }}>
      <SignedOut>
        <Routes>
          <Route path="/" element={cfg.features.landing_page ? <Landing /> : <Navigate to="/login" replace />} />
          <Route
            path="/login"
            element={
              <AuthLayout title="Welcome back" subtitle="Sign in to your Probity workspace.">
                <SignIn routing="hash" signUpUrl={cfg.auth.sign_up ? "/sign-up" : undefined} appearance={CLERK_APPEARANCE} />
              </AuthLayout>
            }
          />
          {cfg.auth.sign_up && (
            <Route
              path="/sign-up"
              element={
                <AuthLayout title="Create an account" subtitle="Use the work email your owner invited.">
                  <SignUp routing="hash" signInUrl="/login" appearance={CLERK_APPEARANCE} />
                </AuthLayout>
              }
            />
          )}
          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
      </SignedOut>
      <SignedIn>
        {user ? (
          <Routes>
            {cfg.features.landing_page && <Route path="/" element={<Landing signedIn />} />}
            <Route path="*" element={<AppRoutes />} />
          </Routes>
        ) : <Centered>{err ? <div className="card p-4 text-sm text-high">Could not load your account: {err}</div> : <Spinner size={20} />}</Centered>}
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
  // The stored login only has the basic user; /me adds the workspace.
  useEffect(() => {
    if (user && !user.workspace) api<Me>("/me").then(setUser).catch(() => {});
  }, [user?.id]);
  const cfg = useAppConfig();
  const signOut = () => {
    setLocalSession(null);
    setUser(null);
  };
  return (
    <AuthCtx.Provider value={{ user, mode: "local", setUser, signOut }}>
      <Routes>
        <Route path="/login" element={user ? <Navigate to="/dashboard" replace /> : <Login />} />
        <Route path="/sign-up" element={user ? <Navigate to="/dashboard" replace /> : <SignUpLocal />} />
        <Route path="/forgot-password" element={user ? <Navigate to="/dashboard" replace /> : <ForgotPasswordLocal />} />
        <Route path="/" element={!cfg.features.landing_page ? <Navigate to={user ? "/dashboard" : "/login"} replace /> : <Landing signedIn={!!user} />} />
        <Route path="*" element={user ? <AppRoutes /> : <Navigate to="/login" replace />} />
      </Routes>
    </AuthCtx.Provider>
  );
}

function Root() {
  const [mode, setMode] = useState<"local" | "clerk" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    // Load /app/config once before rendering so demo-only UI never flashes in and out.
    loadAppConfig().then((cfg) => {
      if (!isFallbackConfig(cfg)) return setMode(cfg.auth.mode);
      // Config unavailable: demo parts stay hidden; the older endpoint still tells us the sign-in mode.
      fetch("/api/v1/auth/config").then((r) => r.json()).then((c) => setMode(c.mode)).catch(() => setErr("The Probity API is unreachable."));
    });
  }, []);
  // The public landing page is static; keep it up even when the API is down.
  if (err && window.location.pathname === "/") return <BrowserRouter><Landing /></BrowserRouter>;
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
