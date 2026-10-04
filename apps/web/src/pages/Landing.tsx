import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { MotionConfig, motion, useReducedMotion, useScroll, useTransform, type MotionValue } from "framer-motion";
import { ArrowRight, ArrowUpRight, Calculator, FileCheck2, Menu, Search, UserCheck, X } from "lucide-react";
import { Button } from "@/components/ui/button";

const HERO_VIDEO =
  "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260307_083826_e938b29f-a43a-41ec-a153-3d4730578ab8.mp4";
const REPO_URL = "https://github.com/sujalmallick/probity-ai";
const DOCS = (f: string) => `${REPO_URL}/blob/real-data/docs/${f}`;

const NAV: { label: string; href?: string; to?: string; external?: boolean }[] = [
  { label: "How it works", href: "#how" },
  { label: "Product", href: "#product" },
  { label: "Principle", href: "#principle" },
  { label: "How to use", to: "/guide" },
  { label: "Docs", href: DOCS("Architecture.md"), external: true },
];

/** Jump to a section on this page. Done in code (not a bare #hash link) so the mobile menu can close first and the
 *  section lands just under the fixed header every time, even when tapped twice. */
function jumpTo(hash: string) {
  const el = document.getElementById(hash.slice(1));
  if (!el) return;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  history.replaceState(null, "", hash);
}

// The rule every Probity case follows (README, "The LLM can investigate…").
const PRINCIPLE =
  "The model can investigate, but it cannot touch the risk score. Agents find signals, every claim has to carry evidence and pass verification, code computes the score, and a person decides the payment.";

const STEPS = [
  { icon: Search, title: "Investigate", body: "Agents read the invoice and check it against your history, public registries and the web." },
  { icon: FileCheck2, title: "Prove", body: "Every claim must cite its source and pass verification, or it doesn't count." },
  { icon: Calculator, title: "Score", body: "Plain code adds up the verified signals. The model can't move the number." },
  { icon: UserCheck, title: "Decide", body: "Low risk clears on its own. Anything else waits for a person." },
];

function GitHubIcon({ size = 18 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="currentColor" aria-hidden>
      <path d="M12 .5a11.5 11.5 0 0 0-3.64 22.41c.58.1.79-.25.79-.56v-2c-3.2.7-3.88-1.37-3.88-1.37-.52-1.33-1.28-1.69-1.28-1.69-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.7 1.26 3.36.96.1-.75.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.29 1.19-3.1-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.18 1.18a11 11 0 0 1 5.79 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.59.23 2.76.11 3.05.74.81 1.19 1.84 1.19 3.1 0 4.42-2.7 5.39-5.26 5.68.41.36.78 1.06.78 2.14v3.17c0 .31.21.67.8.56A11.5 11.5 0 0 0 12 .5Z" />
    </svg>
  );
}

function Brand({ small }: { small?: boolean }) {
  return (
    <span className="flex items-center gap-2.5">
      <img src="/landing/logo.svg" alt="" className={small ? "h-6 w-6" : "h-7 w-7"} />
      <span className={`${small ? "text-base" : "text-lg"} font-bold tracking-tight`}>Probity</span>
    </span>
  );
}

function Navbar({ signInTo, signedIn }: { signInTo: string; signedIn: boolean }) {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  const glass = scrolled || open;
  return (
    <header className="fixed inset-x-0 top-0 z-50 px-3 pt-3 md:px-6">
      <nav
        aria-label="Main"
        className={`mx-auto flex max-w-6xl items-center justify-between rounded-2xl px-3 py-2 transition-[background-color,box-shadow,backdrop-filter] duration-300 md:px-4 ${
          glass ? "bg-black/55 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.09)] backdrop-blur-xl" : ""
        }`}
      >
        <a href="#top" className="rounded-md" aria-label="Probity home" onClick={(e) => { e.preventDefault(); setOpen(false); window.scrollTo({ top: 0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }); history.replaceState(null, "", "#top"); }}><Brand /></a>
        <ul className="hidden items-center gap-1 md:flex">
          {NAV.map((n) => {
            const cls = "flex items-center gap-1 rounded-full px-3.5 py-2 text-sm text-muted-foreground transition-colors duration-200 hover:bg-white/[0.06] hover:text-foreground";
            return (
              <li key={n.label}>
                {n.to ? <Link to={n.to} className={cls}>{n.label}</Link> : (
                  <a
                    href={n.href}
                    {...(n.external ? { target: "_blank", rel: "noreferrer" } : { onClick: (e: React.MouseEvent) => { e.preventDefault(); jumpTo(n.href!); } })}
                    className={cls}
                  >
                    {n.label}
                    {n.external && <ArrowUpRight size={13} aria-hidden />}
                  </a>
                )}
              </li>
            );
          })}
        </ul>
        <div className="flex items-center gap-1.5">
          <a href={REPO_URL} target="_blank" rel="noreferrer" aria-label="Probity on GitHub" className="hidden rounded-full p-2 text-muted-foreground transition-colors duration-200 hover:text-foreground md:block">
            <GitHubIcon />
          </a>
          {!signedIn && (
            <Link to={signInTo} className="hidden rounded-full px-3.5 py-2 text-sm font-medium text-muted-foreground transition-colors duration-200 hover:text-foreground sm:block">
              Sign in
            </Link>
          )}
          <Button asChild className="!rounded-full">
            <Link to={signInTo}>{signedIn ? "Open dashboard" : "Get started"}</Link>
          </Button>
          <button
            className="ml-0.5 rounded-full p-2 text-foreground md:hidden"
            aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open}
            aria-controls="mobile-menu"
            onClick={() => setOpen(!open)}
          >
            {open ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </nav>
      {open && (
        <div id="mobile-menu" className="mx-auto mt-2 max-w-6xl rounded-2xl bg-black/80 p-2 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.09)] backdrop-blur-xl md:hidden">
          <ul className="flex flex-col">
            {NAV.map((n) => {
              const cls = "flex items-center justify-between rounded-xl px-4 py-3 text-base text-foreground hover:bg-white/[0.06]";
              const icon = n.external ? <ArrowUpRight size={16} aria-hidden /> : <ArrowRight size={16} className="text-muted-foreground" aria-hidden />;
              return (
                <li key={n.label}>
                  {n.to ? <Link to={n.to} className={cls} onClick={() => setOpen(false)}>{n.label}{icon}</Link> : (
                    <a
                      href={n.href}
                      {...(n.external ? { target: "_blank", rel: "noreferrer" } : {})}
                      onClick={(e) => {
                        setOpen(false);
                        if (n.external) return;
                        e.preventDefault();
                        // Wait for the menu to close before scrolling, so the jump is measured against the final layout.
                        requestAnimationFrame(() => requestAnimationFrame(() => jumpTo(n.href!)));
                      }}
                      className={cls}
                    >
                      {n.label}{icon}
                    </a>
                  )}
                </li>
              );
            })}
            <li>
              <a href={REPO_URL} target="_blank" rel="noreferrer" className="flex items-center justify-between rounded-xl px-4 py-3 text-base hover:bg-white/[0.06]">
                GitHub <GitHubIcon size={16} />
              </a>
            </li>
            <li className="mt-1 border-t border-border px-2 pt-3 pb-1">
              <Link to={signInTo} className="block rounded-xl px-2 py-2 text-center text-base text-muted-foreground hover:text-foreground">{signedIn ? "Open dashboard" : "Sign in"}</Link>
            </li>
          </ul>
        </div>
      )}
    </header>
  );
}

/** Phones get no parallax: content moving at a different speed from the page made menu jumps land in the wrong place. */
function isSmallScreen() {
  return typeof window !== "undefined" && window.matchMedia("(max-width: 767px)").matches;
}

function Hero({ signInTo }: { signInTo: string }) {
  const sectionRef = useRef<HTMLElement>(null);
  const reduce = useReducedMotion() || isSmallScreen();
  const { scrollYProgress } = useScroll({ target: sectionRef, offset: ["start start", "end start"] });
  const textY = useTransform(scrollYProgress, [0, 1], [0, reduce ? 0 : -200]);
  const textOpacity = useTransform(scrollYProgress, [0, 0.5], [1, reduce ? 1 : 0]);
  const dashY = useTransform(scrollYProgress, [0, 1], [0, reduce ? 0 : -250]);

  return (
    <section ref={sectionRef} id="top" className="relative overflow-hidden pt-28 md:min-h-screen md:pt-36">
      <motion.div style={{ y: textY, opacity: textOpacity }} className="relative z-20 flex flex-col items-center px-4 text-center">
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0 }}
          className="liquid-glass mb-6 flex items-center gap-2 rounded-lg px-3 py-2"
        >
          <span className="rounded-md bg-foreground px-2 py-0.5 text-sm font-medium text-background">New</span>
          <span className="text-sm font-medium text-muted-foreground">Case memory and PDF case reports</span>
        </motion.div>

        <motion.h1
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.1 }}
          className="mb-3 text-5xl leading-tight font-medium tracking-[-2px] md:text-7xl md:leading-[1.15]"
        >
          Every Invoice.
          <br />
          One Clear <span className="font-serif font-normal italic">Verdict</span>.
        </motion.h1>

        <motion.p
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.2 }}
          className="mb-8 text-lg leading-6 font-normal text-hero-subtitle opacity-90"
        >
          Probity investigates every payment, verifies the evidence,{" "}
          <br className="hidden sm:block" />
          and asks you only when the risk is real.
        </motion.p>

        <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6, delay: 0.3 }}>
          <motion.div whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.98 }} className="inline-block">
            <Button asChild size="pill">
              <Link to={signInTo}>Start an Investigation</Link>
            </Button>
          </motion.div>
        </motion.div>
      </motion.div>

      <motion.div
        id="product"
        initial={{ opacity: 0, y: 40 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.8, delay: 0.4 }}
        className="relative mt-10 w-screen scroll-mt-24 md:mt-14"
        style={{ marginLeft: "calc(-50vw + 50%)", aspectRatio: "16 / 9" }}
      >
        <video
          className="absolute inset-0 h-full w-full object-cover"
          src={HERO_VIDEO}
          autoPlay={!reduce}
          muted
          loop
          playsInline
          preload={reduce ? "metadata" : "auto"}
          aria-hidden
        />
        <div className="absolute inset-0 z-10 flex items-center justify-center">
          <motion.div style={{ y: dashY }} className="relative w-[90%] max-w-5xl">
            <img
              src="/landing/hero-dashboard.webp"
              alt="The Probity dashboard with sample data: key numbers, invoices that need a decision, the case queue and the risk mix"
              width={2048}
              height={1280}
              style={{ mixBlendMode: "luminosity" }}
              className="w-full rounded-2xl"
            />
            {/* The screenshot uses invented vendors and figures; say so rather than imply real usage. */}
            <span className="absolute right-3 bottom-3 md:right-4 md:bottom-4">
              <span className="liquid-glass block rounded-md px-2 py-1 text-[11px] text-muted-foreground">Sample data</span>
            </span>
          </motion.div>
        </div>
      </motion.div>

      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-30 h-40 bg-gradient-to-t from-background to-transparent" />
    </section>
  );
}

function HowItWorks() {
  return (
    <section id="how" className="scroll-mt-0 px-6 py-24 md:px-28 md:py-32">
      <div className="mx-auto max-w-6xl">
        <div className="mb-12 flex flex-col items-start gap-3 md:mb-16">
          <span className="liquid-glass rounded-lg px-3 py-1.5 text-sm font-medium text-muted-foreground">How it works</span>
          <h2 className="max-w-2xl text-4xl leading-tight font-medium tracking-[-1.5px] md:text-5xl">
            From invoice to <span className="font-serif font-normal italic">verdict</span>, with receipts.
          </h2>
        </div>
        <ol className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((s, i) => (
            <motion.li
              key={s.title}
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-80px" }}
              transition={{ duration: 0.5, delay: i * 0.08 }}
              className="flex flex-col gap-5 rounded-2xl border border-border bg-card p-6"
            >
              <div className="flex items-center justify-between">
                <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/[0.06]"><s.icon size={19} aria-hidden /></span>
                <span className="text-sm tabular-nums text-muted-foreground">0{i + 1}</span>
              </div>
              <div>
                <h3 className="text-lg font-medium">{s.title}</h3>
                <p className="mt-1.5 text-sm leading-6 text-muted-foreground">{s.body}</p>
              </div>
            </motion.li>
          ))}
        </ol>
      </div>
    </section>
  );
}

function Word({ word, i, total, progress }: { word: string; i: number; total: number; progress: MotionValue<number> }) {
  const range = [i / total, (i + 1) / total];
  const opacity = useTransform(progress, range, [0.2, 1]);
  const color = useTransform(progress, range, ["hsl(0 0% 35%)", "hsl(0 0% 100%)"]);
  return <motion.span style={{ opacity, color }} className="mr-[0.3em]">{word}</motion.span>;
}

function Principle() {
  const containerRef = useRef<HTMLQuoteElement>(null);
  const { scrollYProgress } = useScroll({ target: containerRef, offset: ["start end", "end center"] });
  const words = PRINCIPLE.split(" ");
  return (
    <section id="principle" className="flex scroll-mt-0 items-center justify-center px-8 py-24 md:min-h-[85vh] md:px-28 md:py-28">
      <figure className="mx-auto flex max-w-3xl flex-col items-start gap-10">
        <img src="/landing/quote-symbol.svg" alt="" className="h-10 w-14 object-contain" />
        <blockquote ref={containerRef} className="flex flex-wrap text-4xl leading-[1.2] font-medium md:text-5xl">
          {words.map((w, i) => (
            <Word key={i} word={w} i={i} total={words.length} progress={scrollYProgress} />
          ))}
          <span className="ml-2 text-muted-foreground" aria-hidden>”</span>
        </blockquote>
        <figcaption className="flex items-center gap-4">
          <img src="/landing/logo.svg" alt="" className="h-14 w-14 rounded-full border-[3px] border-foreground object-cover" />
          <div>
            <div className="text-base leading-7 font-semibold text-foreground">The Probity rule</div>
            <div className="text-sm leading-5 font-normal text-muted-foreground">Claim → evidence → verification → score → human</div>
          </div>
        </figcaption>
      </figure>
    </section>
  );
}

const FOOTER_COLUMNS = [
  {
    title: "Product",
    links: [
      { label: "How it works", href: "#how" },
      { label: "Product tour", href: "#product" },
      { label: "The Probity rule", href: "#principle" },
      { label: "How to use", to: "/guide" },
      { label: "Sign in", to: "/login" },
    ],
  },
  {
    title: "Docs",
    links: [
      { label: "Architecture", href: DOCS("Architecture.md") },
      { label: "Guardrails", href: DOCS("Guardrails.md") },
      { label: "Security", href: DOCS("Security.md") },
      { label: "API", href: DOCS("API.md") },
    ],
  },
  {
    title: "Project",
    links: [
      { label: "Source code", href: REPO_URL },
      { label: "README", href: `${REPO_URL}#readme` },
    ],
  },
];

function FooterLink({ label, href, to }: { label: string; href?: string; to?: string }) {
  const cls = "inline-flex items-center gap-1 rounded text-sm text-muted-foreground transition-colors duration-200 hover:text-foreground";
  if (to) return <Link to={to} className={cls}>{label}</Link>;
  const external = href!.startsWith("http");
  return (
    <a href={href} className={cls} {...(external ? { target: "_blank", rel: "noreferrer" } : {})}>
      {label}
      {external && <ArrowUpRight size={13} aria-hidden />}
    </a>
  );
}

function Footer({ signInTo }: { signInTo: string }) {
  return (
    <footer className="relative overflow-hidden px-4 pt-8 md:px-6">
      <div className="relative mx-auto max-w-6xl overflow-hidden rounded-3xl border border-border bg-card px-8 py-14 md:px-14 md:py-20">
        <div aria-hidden className="pointer-events-none absolute -top-1/2 left-1/2 h-[120%] w-[80%] -translate-x-1/2 rounded-full bg-white/[0.05] blur-[100px]" />
        <div className="relative flex flex-col items-start justify-between gap-8 md:flex-row md:items-end">
          <div>
            <h2 className="max-w-xl text-4xl leading-tight font-medium tracking-[-1.5px] md:text-5xl">
              Put evidence before <span className="font-serif font-normal italic">every</span> payment.
            </h2>
            <p className="mt-3 text-base text-muted-foreground">Open source. Every payment decision stays with a person.</p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button asChild size="pill">
              <Link to={signInTo}>Start an Investigation</Link>
            </Button>
            <a href={REPO_URL} target="_blank" rel="noreferrer" className="liquid-glass inline-flex items-center gap-2 rounded-full px-6 py-3.5 text-base font-medium transition-opacity duration-200 hover:opacity-80">
              <GitHubIcon size={17} /> View source
            </a>
          </div>
        </div>
      </div>

      <div id="contact" className="mx-auto grid max-w-6xl gap-10 px-4 py-16 md:grid-cols-[2fr_1fr_1fr_1fr]">
        <div className="flex max-w-sm flex-col gap-4">
          <Brand />
          <p className="text-sm leading-6 text-muted-foreground">
            An AI investigation team for small-business payments. It checks each invoice, shows its evidence, and asks a person only when the risk warrants it.
          </p>
          <a href={REPO_URL} target="_blank" rel="noreferrer" aria-label="Probity on GitHub" className="w-fit rounded-full border border-border p-2 text-muted-foreground transition-colors duration-200 hover:border-white/40 hover:text-foreground">
            <GitHubIcon size={16} />
          </a>
        </div>
        {FOOTER_COLUMNS.map((col) => (
          <nav key={col.title} aria-label={col.title} className="flex flex-col gap-3">
            <div className="text-sm font-semibold">{col.title}</div>
            <ul className="flex flex-col gap-2.5">
              {col.links.map((l) => (
                <li key={l.label}><FooterLink {...l} /></li>
              ))}
            </ul>
          </nav>
        ))}
      </div>

      <div className="mx-auto flex max-w-6xl flex-col gap-2 border-t border-border px-4 py-6 text-xs text-muted-foreground md:flex-row md:items-center md:justify-between">
        <span>© {new Date().getFullYear()} Probity. Evidence before payment.</span>
        <span>Probity never moves money. Every payment decision stays with a person.</span>
      </div>

      <div
        aria-hidden
        className="pointer-events-none mx-auto h-[0.72em] max-w-6xl overflow-hidden bg-gradient-to-b from-white/[0.14] to-transparent bg-clip-text text-center leading-[0.95] font-semibold tracking-[-0.06em] text-transparent select-none"
        style={{ fontSize: "clamp(5rem, 20vw, 18rem)" }}
      >
        Probity
      </div>
    </footer>
  );
}

export default function Landing({ signedIn = false }: { signedIn?: boolean }) {
  const signInTo = signedIn ? "/dashboard" : "/login";
  return (
    <MotionConfig reducedMotion="user">
      <div className="landing min-h-screen overflow-x-clip font-sans">
        <Navbar signInTo={signInTo} signedIn={signedIn} />
        <main>
          <Hero signInTo={signInTo} />
          <HowItWorks />
          <Principle />
        </main>
        <Footer signInTo={signInTo} />
      </div>
    </MotionConfig>
  );
}
