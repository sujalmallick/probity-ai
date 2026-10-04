"""Check every integration for real, one small request each.

    python -m probity.check                      # all checks
    python -m probity.check database crud        # database health and a create/read/update/delete round trip
    python -m probity.check ai web_search        # only these
    python -m probity.check domain_lookup --domain yourvendor.in
    python -m probity.check email --send-test-email   # sends ONE email to the first EMAIL_ALLOWLIST address

Prints OK / MISSING / FAILED / UNAVAILABLE / SKIPPED per integration with a short, key-free reason. Exit code 0 when
nothing FAILED. Nothing here writes business data: the storage check writes one test object and deletes it, and the
email check sends only when asked, only to your own allowlisted address.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel, Field

from probity.config import get_settings


@dataclass
class Result:
    name: str
    status: str  # ok | missing | failed | unavailable | skipped
    detail: str
    extra: dict = field(default_factory=dict)


def _timed(fn: Callable[[], Result]) -> Result:
    t0 = time.perf_counter()
    try:
        r = fn()
    except Exception as e:  # noqa: BLE001 - a check must report, never crash
        r = Result(fn.__name__.removeprefix("check_"), "failed", f"{type(e).__name__}: {str(e)[:200]}")
    r.extra.setdefault("ms", round((time.perf_counter() - t0) * 1000))
    return r


# ---------------------------------------------------------------- database

def check_database() -> Result:
    from sqlalchemy import text

    from probity.db.migrate import pending_migrations
    from probity.db.session import get_engine

    st = get_settings()
    if not st.database_url:
        return Result("database", "missing", "DATABASE_URL is not set")
    with get_engine().connect() as c:
        role, superuser = c.execute(text("SELECT current_user, rolsuper FROM pg_roles WHERE rolname = current_user")).one()
        rls = c.execute(text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = 'cases'")).scalar()
    pending = pending_migrations()
    problems = []
    if superuser:
        problems.append(f"the app connects as superuser '{role}', which bypasses row-level security")
    if not rls:
        problems.append("row-level security is not enforced on cases")
    if pending:
        problems.append(f"schema {pending} (run python -m probity.bootstrap)")
    if problems:
        return Result("database", "failed", "; ".join(problems))
    return Result("database", "ok", f"connected as '{role}' (not superuser), row-level security enforced, schema up to date")


def check_crud() -> Result:
    """Create, read (on a separate connection), update and delete one vendor in a throwaway workspace, as the app's
    own database role, then remove the workspace. Proves writes are committed and row-level security lets the app
    see only the workspace it is acting for."""
    import uuid

    from sqlalchemy import select

    from probity.db.models import Vendor, Workspace
    from probity.db.session import session_scope

    ws_id = f"ws_check_{uuid.uuid4().hex[:10]}"
    vendor_id = None
    try:
        with session_scope(ws_id) as s:
            s.add(Workspace(id=ws_id, name="Probity integration check (temporary)", policy={}))
            s.flush()
            v = Vendor(workspace_id=ws_id, name="Check Vendor")
            s.add(v)
            s.flush()
            vendor_id = v.id
        with session_scope(ws_id) as s:  # new connection: only committed data is visible
            if s.get(Vendor, vendor_id) is None:
                return Result("crud", "failed", "a committed vendor was not readable on a new connection")
        with session_scope("ws_someone_else") as s:
            if s.scalars(select(Vendor).where(Vendor.id == vendor_id)).first() is not None:
                return Result("crud", "failed", "another workspace could read the vendor (row-level security not applied)")
        with session_scope(ws_id) as s:
            s.get(Vendor, vendor_id).name = "Check Vendor (updated)"
        with session_scope(ws_id) as s:
            if s.get(Vendor, vendor_id).name != "Check Vendor (updated)":
                return Result("crud", "failed", "an update was not persisted")
            s.delete(s.get(Vendor, vendor_id))
        with session_scope(ws_id) as s:
            if s.get(Vendor, vendor_id) is not None:
                return Result("crud", "failed", "a delete was not persisted")
            s.delete(s.get(Workspace, ws_id))
        return Result("crud", "ok", "create, read on a new connection, update and delete all persisted; other workspaces can't see the row")
    finally:  # never leave the temporary workspace behind
        with session_scope(ws_id) as s:
            if vendor_id and (v := s.get(Vendor, vendor_id)) is not None:
                s.delete(v)
            if (w := s.get(Workspace, ws_id)) is not None:
                s.delete(w)


# ---------------------------------------------------------------- AI

class _Ping(BaseModel):
    ok: bool
    word: str = Field(max_length=20)


def check_ai() -> Result:
    from probity.llm import client as llm

    st = get_settings()
    if not st.llm_api_key:
        return Result("ai", "missing", f"{st.llm_key_name} is not set (LLM_PROVIDER={st.llm_provider})")
    out = []
    for tier, model in (("fast", st.llm_model("fast")), ("reasoning", st.llm_model("reasoning"))):
        try:
            r = llm.generate(schema=_Ping, system="This is a connectivity check. Reply with ok=true and word='ready'.",
                             user="ping", tier=tier, tags={"agent": "integration_check", "prompt_version": "check"})  # type: ignore[arg-type]
        except llm.LLMFailed as e:
            return Result("ai", "failed", f"{model}: {e.reason}")
        if not r.ok:
            return Result("ai", "failed", f"{model}: answered but not with the expected structured output")
        out.append(model)
    return Result("ai", "ok", f"{st.llm_provider}: structured output works with " + " and ".join(out))


# ---------------------------------------------------------------- sign-in (Clerk)

def check_sign_in() -> Result:
    st = get_settings()
    if not (st.clerk_issuer and st.clerk_secret_key):
        return Result("sign_in", "missing", "CLERK_ISSUER and CLERK_SECRET_KEY must both be set")
    url = st.clerk_jwks_url or f"{st.clerk_issuer.rstrip('/')}/.well-known/jwks.json"
    r = httpx.get(url, timeout=10)
    if r.status_code != 200 or not r.json().get("keys"):
        return Result("sign_in", "failed", f"could not load signing keys from {url} (HTTP {r.status_code}); check CLERK_ISSUER")
    r = httpx.get("https://api.clerk.com/v1/users", params={"limit": 1}, headers={"Authorization": f"Bearer {st.clerk_secret_key}"}, timeout=10)
    if r.status_code in (401, 403):
        return Result("sign_in", "failed", "Clerk rejected CLERK_SECRET_KEY")
    if r.status_code >= 300:
        return Result("sign_in", "failed", f"Clerk Backend API returned HTTP {r.status_code}")
    parties = [p.strip() for p in st.clerk_authorized_parties.split(",") if p.strip()]
    origin = st.public_app_url.rstrip("/")
    note = "" if origin in parties else f"; note: PUBLIC_APP_URL {origin} is not in CLERK_AUTHORIZED_PARTIES, so sign-ins from it will be refused"
    return Result("sign_in", "ok", f"signing keys loaded from the issuer, secret key accepted, authorized parties: {', '.join(parties)}{note}")


# ---------------------------------------------------------------- web search (Tavily) + SSRF guard

def check_web_search(query: str = "Reserve Bank of India") -> Result:
    from probity.tools import lookups
    from probity.tools.base import Budget
    from probity.tools.fetch import FetchBlocked, check_url

    for bad in ("http://169.254.169.254/latest/meta-data/", "http://127.0.0.1/", "http://10.0.0.1/", "file:///etc/passwd"):
        try:
            check_url(bad)
            return Result("web_search", "failed", f"SSRF guard let {bad} through")
        except FetchBlocked:
            pass
    st = get_settings()
    if not st.tavily_api_key:
        return Result("web_search", "missing", "TAVILY_API_KEY is not set; external reputation reports 'could not verify' (SSRF guard OK)")
    budget = Budget.from_settings()
    res = lookups.web_search(query, budget)
    if res.status != "ok":
        return Result("web_search", "failed", res.reason)
    if not res.hits:
        return Result("web_search", "ok", f"search answered with no results for '{query}' (SSRF guard OK)")
    page = lookups.fetch_page(res.hits[0].url, budget)
    fetched = f"read {res.hits[0].url}" if page.status == "ok" else f"could not read the first result ({page.reason})"
    return Result("web_search", "ok", f"{len(res.hits)} result(s) for '{query}'; {fetched}; SSRF guard OK")


# ---------------------------------------------------------------- domain registration (RDAP)

def check_domain_lookup(domain: str = "rbi.org.in") -> Result:
    from probity.tools import lookups
    from probity.tools.base import Budget

    r = lookups.rdap_lookup(domain, Budget.from_settings())
    if r.status != "ok":
        return Result("domain_lookup", "failed", f"{domain}: {r.reason}")
    return Result("domain_lookup", "ok", f"{domain} registered {r.created} ({r.registrar or 'registrar unknown'})")


# ---------------------------------------------------------------- GST

def check_gst_registry() -> Result:
    from probity.ingestion.validators import make_gstin, valid_gstin
    from probity.tools.lookups import GST_REGISTRY_REASON

    g = make_gstin("27", "AAAAA0000A")
    if not valid_gstin(g) or valid_gstin(g[:-1] + ("0" if g[-1] != "0" else "1")):
        return Result("gst_registry", "failed", "GSTIN checksum self-test failed")
    return Result("gst_registry", "unavailable", f"{GST_REGISTRY_REASON}. Format and checksum are validated; registry status shows 'could not verify'")


# ---------------------------------------------------------------- email (Resend) + allowlist

def check_email(send_test_email: bool = False) -> Result:
    from probity import mailer

    st = get_settings()
    if not mailer.configured():
        return Result("email", "missing", "RESEND_API_KEY and EMAIL_FROM are not both set; nothing is sent")
    allow = sorted(st.email_allowlist_set)
    if not st.email_send_to_any:
        try:
            mailer.check_recipient("someone@not-on-the-allowlist.invalid")
            return Result("email", "failed", "the allowlist did not block an outside address")
        except mailer.MailBlocked:
            pass
        if not allow:
            return Result("email", "failed", "EMAIL_ALLOWLIST is empty, so every email would be blocked")
    mode = "any recipient (EMAIL_SEND_TO_ANY=true)" if st.email_send_to_any else f"allowlist only ({len(allow)} address(es))"
    if not send_test_email:
        return Result("email", "ok", f"configured; {mode}; add --send-test-email to send one test message to your first allowlisted address")
    if not allow:
        return Result("email", "failed", "--send-test-email needs an address in EMAIL_ALLOWLIST")
    to = allow[0]
    mid = mailer._send(to, "Probity integration check", "This is a test message from `python -m probity.check email --send-test-email`.")
    return Result("email", "ok", f"test message sent to {mailer.masked(to)} (Resend id {mid}); {mode}")


# ---------------------------------------------------------------- storage, limits, antivirus

def check_storage() -> Result:
    import hashlib

    from probity import storage

    st = get_settings()
    data = b"probity storage check"
    ref = storage.put("_healthcheck", hashlib.sha256(data).hexdigest(), data, "text/plain")
    try:
        ok = storage.get(ref) == data
    finally:
        storage.delete(ref)
    if not ok:
        return Result("storage", "failed", "wrote a test object but read back different bytes")
    where = f"S3 bucket {st.s3_bucket}" if st.storage_backend == "s3" else f"local disk {st.storage_dir}"
    return Result("storage", "ok", f"write/read/delete round trip OK on {where}; uploads up to {st.max_upload_mb} MB")


EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def check_antivirus() -> Result:
    from probity.ingestion.scan import InfectedFile, clamav_scan

    st = get_settings()
    if not st.clamav_host:
        return Result("antivirus", "skipped", "optional, not configured (CLAMAV_HOST); every uploaded PDF is cleaned of active content instead")
    if clamav_scan(b"clean probity check") != "OK":
        return Result("antivirus", "failed", "clean test data was not reported OK")
    try:
        clamav_scan(EICAR)
    except InfectedFile:
        return Result("antivirus", "ok", f"ClamAV at {st.clamav_host}:{st.clamav_port} passes clean data and blocks the EICAR test file")
    return Result("antivirus", "failed", "the EICAR test file was not detected")


CHECKS: dict[str, Callable[..., Result]] = {
    "database": check_database, "crud": check_crud, "ai": check_ai, "sign_in": check_sign_in, "web_search": check_web_search,
    "domain_lookup": check_domain_lookup, "gst_registry": check_gst_registry, "email": check_email,
    "storage": check_storage, "antivirus": check_antivirus,
}


def run(names: list[str] | None = None, *, domain: str | None = None, query: str | None = None, send_test_email: bool = False) -> list[Result]:
    results = []
    for name in names or list(CHECKS):
        fn = CHECKS[name]
        kwargs = {"domain_lookup": {"domain": domain} if domain else {}, "web_search": {"query": query} if query else {},
                  "email": {"send_test_email": send_test_email}}.get(name, {})
        r = _timed(lambda fn=fn, kwargs=kwargs: fn(**kwargs))  # type: ignore[misc]
        r.name = name
        results.append(r)
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m probity.check", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("checks", nargs="*", help=f"any of: {', '.join(CHECKS)} (default: all)")
    ap.add_argument("--domain", help="domain for the RDAP check (default rbi.org.in)")
    ap.add_argument("--query", help="query for the web search check")
    ap.add_argument("--send-test-email", action="store_true", help="send one test email to the first EMAIL_ALLOWLIST address")
    args = ap.parse_args(argv)
    unknown = [c for c in args.checks if c not in CHECKS]
    if unknown:
        ap.error(f"unknown check(s): {', '.join(unknown)}; choose from {', '.join(CHECKS)}")
    results = run(args.checks or None, domain=args.domain, query=args.query, send_test_email=args.send_test_email)
    marks = {"ok": "OK  ", "missing": "MISS", "failed": "FAIL", "unavailable": "N/A ", "skipped": "SKIP"}
    for r in results:
        print(f"{marks.get(r.status, r.status):<5} {r.name:<14} {r.detail}  ({r.extra.get('ms')} ms)")
    failed = [r.name for r in results if r.status == "failed"]
    print(f"\n{len(results) - len(failed)}/{len(results)} without failures" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
