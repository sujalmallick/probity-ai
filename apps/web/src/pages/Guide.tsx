import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  ArrowLeft, ArrowRight, BadgeCheck, Building2, ClipboardCheck, FileSearch, FileUp, Gavel, History, LogIn, MailCheck, PhoneCall, ShieldCheck, Users,
} from "lucide-react";
import { Button } from "@/components/ui/button";

type Step = { icon: typeof LogIn; title: string; where?: string; body: ReactNode; tip?: string };

const SETUP: Step[] = [
  {
    icon: LogIn, title: "Sign in and choose your workspace", where: "Sign in",
    body: <>Create an account with your work email. If a colleague invited you, accept their invitation; otherwise start your own workspace and you become its owner.</>,
    tip: "Only accept invitations from people you know.",
  },
  {
    icon: Building2, title: "Add your vendors", where: "Vendors → Add vendor, or Import CSV",
    body: <>Add each supplier with its GSTIN, bank account, email domain and a contact. Every invoice is checked against this list, so the more complete it is, the more Probity can catch.</>,
    tip: "Have many vendors? Download the CSV template on the import page and upload it.",
  },
  {
    icon: BadgeCheck, title: "Verify the details you trust", where: "Vendors → open a vendor → Verify",
    body: <>An approver marks a bank account, domain or contact as verified after confirming it outside email, for example by calling a number already on file. Only verified details are treated as trusted.</>,
  },
  {
    icon: History, title: "Add past invoices and purchase orders", where: "Vendors → Import CSV · Approvals",
    body: <>Recommended. Past invoices let Probity spot price jumps and duplicates; purchase orders let it spot over-billing. Entries made by accountants wait in Approvals until an approver accepts them.</>,
  },
  {
    icon: Users, title: "Invite your team", where: "Team → Invite by email",
    body: <>Give each person a role. Accountants upload invoices, approvers make payment decisions, and the owner manages policy and the team.</>,
  },
];

const CHECK: Step[] = [
  {
    icon: FileUp, title: "Upload an invoice", where: "New case",
    body: <>Drop in the invoice as a PDF. Probity shows what it read (vendor, amount, bank account, dates) so you can correct anything before the check starts.</>,
    tip: "Use the original digital PDF. Photos and scanned images are not accepted.",
  },
  {
    icon: FileSearch, title: "Let Probity investigate", where: "Opens automatically",
    body: <>It reads the document, compares it with your vendor list, history and purchase orders, and for larger amounts checks public sources. This usually takes a minute or two. Open <b>Agent pipeline</b> to watch each step.</>,
  },
  {
    icon: ShieldCheck, title: "Read the result", where: "The case page",
    body: <>You get a score from 0 to 100 and a risk level. Each anomaly shows its evidence. <b>Could not verify</b> means a check couldn't run; the invoice is held for a person, never assumed safe. <b>Why this score?</b> explains every point.</>,
  },
];

const DECIDE: Step[] = [
  {
    icon: Gavel, title: "Make the decision", where: "Your decision panel (approvers)",
    body: <><b>Approve</b> or <b>Reject</b> the payment, <b>Request verification</b> from the vendor, or <b>Investigate further</b>. High and critical invoices need a written reason, and large or critical ones need two approvers.</>,
  },
  {
    icon: MailCheck, title: "Ask the vendor, safely", where: "Request verification → Review draft email",
    body: <>Probity drafts a neutral email to the vendor's verified contact. Nothing is sent until an approver reviews and approves it. If the vendor replies outside Probity, record the reply on the case.</>,
  },
  {
    icon: PhoneCall, title: "Confirm out-of-band", where: "Vendor reply → Confirm out-of-band",
    body: <>A reply alone never lowers the score, because whoever sent a fake invoice may control that inbox too. Call the vendor on a number already on file, then record what they confirmed.</>,
  },
  {
    icon: ClipboardCheck, title: "Close the case", where: "Close case",
    body: <>Record the outcome. It is saved to case memory and shown the next time the same vendor, bank account or domain appears.</>,
  },
];

const LEVELS = [
  { tier: "Low", range: "0–29", color: "var(--low)", text: "Can be cleared without a person, if every required check finished and the amount is within your limit." },
  { tier: "Medium", range: "30–59", color: "var(--medium)", text: "Sent to a person to review." },
  { tier: "High", range: "60–79", color: "var(--high)", text: "Payment held. An approver decides and writes a reason." },
  { tier: "Critical", range: "80–100", color: "var(--critical)", text: "Payment held. Two approvers must agree." },
];

const ROLES = [
  { role: "Viewer", can: "Read cases, vendors and comments." },
  { role: "Accountant", can: "Upload invoices, add vendors and records, comment, record vendor replies." },
  { role: "Approver", can: "Everything above, plus approve or reject payments, verify vendor details, send emails, approve records." },
  { role: "Owner", can: "Everything above, plus the risk policy and the team." },
];

const FAQ: { q: string; a: ReactNode }[] = [
  { q: "Why was my invoice held?", a: <>The case page lists the reasons under the score. Common ones: a new bank account, a price jump, a check that couldn't be verified, or an amount above your auto-clear limit.</> },
  { q: "What does “Could not verify” mean?", a: <>A source was unavailable or data was missing, so that check couldn't run. It never counts as a pass and never adds points; the invoice simply waits for a person.</> },
  { q: "A case says “This investigation stopped”.", a: <>Something interrupted it, for example a server restart. Click <b>Retry from the start</b>; the same document runs again as a new case.</> },
  { q: "The invoice isn't in rupees.", a: <>Probity never converts currencies. Invoices in another currency, or with no stated currency, are held for a person to review.</> },
  { q: "Why can't I click Approve?", a: <>Payment decisions need the approver role. Hover a disabled button to see what it needs, or ask your workspace owner.</> },
  { q: "Does Probity pay invoices?", a: <>No. Probity never moves money. It gives you the evidence; a person makes every payment decision in your own banking system.</> },
];

const TOC = [
  { id: "setup", label: "1. Set up once" },
  { id: "check", label: "2. Check an invoice" },
  { id: "decide", label: "3. Decide" },
  { id: "levels", label: "Risk levels" },
  { id: "roles", label: "Roles" },
  { id: "faq", label: "Common questions" },
];

function StepList({ steps, start }: { steps: Step[]; start: number }) {
  return (
    <ol className="flex flex-col gap-3">
      {steps.map((s, i) => (
        <li key={s.title} className="flex gap-4 rounded-2xl border border-border bg-card/60 p-5">
          <div className="flex shrink-0 flex-col items-center gap-2">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/[0.08]"><s.icon size={18} aria-hidden /></span>
            <span className="text-xs tabular-nums text-muted-foreground">{String(start + i).padStart(2, "0")}</span>
          </div>
          <div className="min-w-0">
            <h3 className="text-base font-medium">{s.title}</h3>
            {s.where && <div className="mt-1 inline-flex rounded-md bg-white/[0.06] px-2 py-0.5 text-xs text-muted-foreground">{s.where}</div>}
            <p className="mt-2 text-sm leading-6 text-muted-foreground [&_b]:font-medium [&_b]:text-foreground">{s.body}</p>
            {s.tip && <p className="mt-2 text-xs text-muted-foreground">Tip: {s.tip}</p>}
          </div>
        </li>
      ))}
    </ol>
  );
}

function Part({ id, eyebrow, title, intro, children }: { id: string; eyebrow: string; title: string; intro: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-24">
      <span className="text-xs font-medium tracking-wider text-muted-foreground uppercase">{eyebrow}</span>
      <h2 className="mt-1 text-2xl font-medium tracking-[-0.6px] md:text-3xl">{title}</h2>
      <p className="mt-2 mb-5 text-sm text-muted-foreground">{intro}</p>
      {children}
    </section>
  );
}

/** "How to use Probity": a public, plain-language walkthrough from first sign-in to a closed case. */
export default function Guide({ signedIn = false }: { signedIn?: boolean }) {
  const cta = signedIn ? "/dashboard" : "/login";
  const [active, setActive] = useState("setup");
  useEffect(() => {
    window.scrollTo(0, 0);
    const els = TOC.map((t) => document.getElementById(t.id)).filter(Boolean) as HTMLElement[];
    const io = new IntersectionObserver((entries) => {
      const vis = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
      if (vis) setActive(vis.target.id);
    }, { rootMargin: "-20% 0px -70% 0px" });
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);
  const go = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });

  return (
    <div className="landing min-h-screen font-sans">
      <header className="sticky top-0 z-40 border-b border-border bg-black/70 backdrop-blur-xl">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-3 md:px-6">
          <Link to="/" className="flex items-center gap-2.5 rounded-md" aria-label="Probity home">
            <img src="/landing/logo.svg" alt="" className="h-7 w-7" />
            <span className="text-lg font-bold tracking-tight">Probity</span>
          </Link>
          <div className="flex items-center gap-1.5">
            <Link to="/" className="hidden items-center gap-1.5 rounded-full px-3 py-2 text-sm text-muted-foreground hover:text-foreground sm:flex"><ArrowLeft size={14} aria-hidden />Home</Link>
            <Button asChild className="!rounded-full"><Link to={cta}>{signedIn ? "Open dashboard" : "Get started"}</Link></Button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-4 pt-12 pb-20 md:px-6 md:pt-16">
        <div className="max-w-3xl">
          <span className="liquid-glass inline-block rounded-lg px-3 py-1.5 text-sm font-medium text-muted-foreground">How to use</span>
          <h1 className="mt-4 text-4xl leading-tight font-medium tracking-[-1.5px] md:text-5xl">
            How to use <span className="font-serif font-normal italic">Probity</span>
          </h1>
          <p className="mt-4 text-base leading-7 text-muted-foreground md:text-lg">
            Probity checks each supplier invoice before you pay it, shows the evidence for anything unusual, and leaves the payment decision to a person. Set it up once, then every invoice takes three steps: upload, read, decide.
          </p>
          <div className="mt-6 grid gap-3 sm:grid-cols-3">
            {[["Upload", "Drop in the invoice PDF."], ["Read", "See the score and the evidence."], ["Decide", "Approve, reject or ask the vendor."]].map(([t, d], i) => (
              <div key={t} className="rounded-2xl border border-border bg-card/60 p-4">
                <div className="text-xs text-muted-foreground tabular-nums">Step {i + 1}</div>
                <div className="mt-1 font-medium">{t}</div>
                <div className="text-sm text-muted-foreground">{d}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="mt-14 grid gap-10 lg:grid-cols-[200px_minmax(0,1fr)]">
          <nav aria-label="On this page" className="hidden lg:block">
            <ul className="sticky top-24 flex flex-col gap-1 text-sm">
              {TOC.map((t) => (
                <li key={t.id}>
                  <button
                    onClick={() => go(t.id)}
                    aria-current={active === t.id ? "true" : undefined}
                    className={`w-full rounded-lg px-3 py-1.5 text-left transition-colors duration-150 ${active === t.id ? "bg-white/[0.07] text-foreground" : "text-muted-foreground hover:text-foreground"}`}
                  >
                    {t.label}
                  </button>
                </li>
              ))}
            </ul>
          </nav>

          <div className="flex min-w-0 flex-col gap-14">
            <Part id="setup" eyebrow="Part 1" title="Set up once" intro="You can start checking invoices before everything is filled in. Probity tells you what it couldn't compare.">
              <StepList steps={SETUP} start={1} />
            </Part>
            <Part id="check" eyebrow="Part 2" title="Check an invoice" intro="Do this for every invoice before it's paid.">
              <StepList steps={CHECK} start={SETUP.length + 1} />
            </Part>
            <Part id="decide" eyebrow="Part 3" title="Decide" intro="Low-risk invoices can clear on their own. Everything else waits here for a person.">
              <StepList steps={DECIDE} start={SETUP.length + CHECK.length + 1} />
            </Part>

            <section id="levels" className="scroll-mt-24">
              <h2 className="text-2xl font-medium tracking-[-0.6px]">Risk levels</h2>
              <p className="mt-2 mb-5 text-sm text-muted-foreground">Points come only from findings backed by evidence. The AI gathers the evidence; fixed rules calculate the score.</p>
              <ul className="grid gap-3 sm:grid-cols-2">
                {LEVELS.map((l) => (
                  <li key={l.tier} className="rounded-2xl border border-border bg-card/60 p-4">
                    <div className="flex items-center justify-between">
                      <span className="flex items-center gap-2 font-medium"><span className="h-2.5 w-2.5 rounded-full" style={{ background: l.color }} aria-hidden />{l.tier}</span>
                      <span className="text-xs tabular-nums text-muted-foreground">{l.range}</span>
                    </div>
                    <p className="mt-2 text-sm text-muted-foreground">{l.text}</p>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-muted-foreground">Your owner sets the limits (auto-clear amount, two-approver amount) on the Policy page.</p>
            </section>

            <section id="roles" className="scroll-mt-24">
              <h2 className="text-2xl font-medium tracking-[-0.6px]">Who can do what</h2>
              <p className="mt-2 mb-5 text-sm text-muted-foreground">Roles are enforced by the server. A button you can't use shows why when you hover it.</p>
              <div className="overflow-hidden rounded-2xl border border-border">
                <table className="w-full text-sm">
                  <tbody>
                    {ROLES.map((r) => (
                      <tr key={r.role} className="border-t border-border first:border-t-0 align-top">
                        <th scope="row" className="w-32 bg-card/60 px-4 py-3 text-left font-medium">{r.role}</th>
                        <td className="px-4 py-3 text-muted-foreground">{r.can}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section id="faq" className="scroll-mt-24">
              <h2 className="text-2xl font-medium tracking-[-0.6px]">Common questions</h2>
              <div className="mt-5 flex flex-col gap-2">
                {FAQ.map((f) => (
                  <details key={f.q} className="group rounded-2xl border border-border bg-card/60 px-5 py-4 [&_b]:font-medium [&_b]:text-foreground">
                    <summary className="flex cursor-pointer list-none items-center justify-between gap-3 font-medium">
                      {f.q}<ArrowRight size={15} className="shrink-0 text-muted-foreground transition-transform duration-150 group-open:rotate-90" aria-hidden />
                    </summary>
                    <p className="mt-2 text-sm leading-6 text-muted-foreground">{f.a}</p>
                  </details>
                ))}
              </div>
            </section>

            <div className="flex flex-col items-start gap-4 rounded-3xl border border-border bg-card p-8 md:flex-row md:items-center md:justify-between">
              <div>
                <h2 className="text-2xl font-medium tracking-[-0.6px]">Ready to check your first invoice?</h2>
                <p className="mt-1 text-sm text-muted-foreground">Probity never moves money. Every payment decision stays with a person.</p>
              </div>
              <Button asChild size="pill"><Link to={signedIn ? "/cases/new" : cta}>{signedIn ? "Upload an invoice" : "Get started"}</Link></Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
