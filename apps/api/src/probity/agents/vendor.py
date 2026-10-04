"""Agent 3 — Vendor Investigator: "Who is this entity; is it the same as claimed?"

A mismatch becomes a claim only if both sides are evidenced. When a source can't be checked (vendor not in the vendor
list, RDAP unreachable, no GST registry provider) the check is recorded as "could not verify" and shown in the
timeline; it never adds risk points and never counts as a pass.
"""

from __future__ import annotations

from datetime import date

from probity import graph_rel
from probity.agents.common import fsnip, fv, load_case, record_claim, vendor_bundle
from probity.db.session import session_scope
from probity.events import CaseCtx
from probity.evidence.models import AgentClaim, EvidenceIn
from probity.signals import detectors as d
from probity.ingestion.validators import valid_gstin
from probity.tools import lookups

AGENT = "vendor_investigator"


def run(ctx: CaseCtx) -> dict:
    ctx.emit("agent.started", agent=AGENT, status="running", message="Identifying vendor")
    checks: dict[str, dict] = {}
    claims: list[str] = []
    entity = "unknown"
    sources = 0
    with session_scope() as s:
        case = load_case(s, ctx)
        ex = case.extraction
        b = vendor_bundle(s, ctx.workspace_id, case.vendor_id)
        v = b["vendor"]
        inv_name, inv_gstin, inv_addr = fv(ex, "vendor_name"), fv(ex, "gstin"), fv(ex, "vendor_address")
        domain = fv(ex, "sender_domain")

        # --- vendor master
        if v is None:
            checks["vendor_identity"] = ctx.unverifiable(AGENT, "vendor_identity", "this vendor is not in your vendor list, so there is nothing to compare the invoice against")
            c = record_claim(s, ctx, AGENT, AgentClaim(
                claim=f"Vendor '{inv_name or 'unnamed'}' is not in the vendor master (unknown vendor).",
                evidence=[EvidenceIn(source="invoice", field="vendor_name", value=inv_name, source_ref="invoice", excerpt=fsnip(ex, "vendor_name"), tier=1)],
                confidence=0.9, severity="info", assertion={"op": "info"},
            ))
            claims.append(c.id)
        else:
            entity = "same"
            ctx.progress(AGENT, f"Vendor master match: {v.name}")
            res = d.identity_mismatch(inv_gstin, v.gstin, inv_name, v.name)
            if res.skipped:
                checks["vendor_identity"] = {**ctx.unverifiable(AGENT, "vendor_identity", res.skipped), "signal": res.signal}
            else:
                checks["vendor_identity"] = {"status": "fired" if res.fired else "passed", "reason": res.detail.get("field", "") or "GSTIN and name match the vendor list", "signal": res.signal}
            if res.fired:
                entity = "different"
                field = res.detail["field"]
                op = {"op": "not_in", "subject": "$0", "set": ["$1"]} if field == "gstin" else {"op": "similarity_below", "a": "$0", "b": "$1", "threshold": 80}
                c = record_claim(s, ctx, AGENT, AgentClaim(
                    claim=f"Invoice {field.replace('_', ' ')} '{res.value}' does not match vendor master '{res.baseline}'.",
                    signal="identity_mismatch",
                    evidence=[
                        EvidenceIn(source="invoice", field=field, value=res.value, source_ref="invoice", excerpt=fsnip(ex, "gstin" if field == "gstin" else "vendor_name"), tier=1),
                        EvidenceIn(source="vendor_master", field=field, value=res.baseline, source_ref=f"vendor:{v.id}", tier=1),
                    ],
                    confidence=0.95, severity=res.severity, assertion=op,
                    data={"observed": res.value, "baseline": res.baseline},
                ))
                claims.append(c.id)
            ares = d.address_mismatch(inv_addr, v.address)
            checks["address_match"] = {"status": "fired" if ares.fired else ("skipped" if ares.skipped else "passed"), "reason": ares.skipped or f"similarity {ares.detail.get('similarity')}", "signal": ares.signal}
            if ares.fired:
                c = record_claim(s, ctx, AGENT, AgentClaim(
                    claim="Vendor address on invoice does not match the vendor master address.",
                    signal="address_mismatch",
                    evidence=[
                        EvidenceIn(source="invoice", field="vendor_address", value=inv_addr, source_ref="invoice", excerpt=fsnip(ex, "vendor_address"), tier=1),
                        EvidenceIn(source="vendor_master", field="address", value=v.address, source_ref=f"vendor:{v.id}", tier=1),
                    ],
                    confidence=0.85, severity="warn", assertion={"op": "similarity_below", "a": "$0", "b": "$1", "threshold": 70},
                    data={"observed": inv_addr, "baseline": v.address},
                ))
                claims.append(c.id)

        # --- relationship graph: does another vendor share this bank account / domain / address?
        if v is not None:
            ctx.progress(AGENT, "Checking relationship graph for shared bank, domain or address")
            attrs = graph_rel.index_invoice(s, ctx.workspace_id, v.id, ctx.case_id, ex)
            shared = graph_rel.shared_with_other_vendors(s, ctx.workspace_id, v.id, attrs)
            checks["relationship_check"] = {"status": "fired" if shared else "passed", "reason": f"{len(shared)} shared attribute(s)", "signal": "shared_attribute"}
            for h in shared[:3]:
                c = record_claim(s, ctx, AGENT, AgentClaim(
                    claim=f"The {h['kind']} {h['label']} on this invoice is also linked to a different vendor: {h['other_vendor']}.",
                    signal="shared_attribute",
                    evidence=[
                        EvidenceIn(source="invoice", field=h["kind"].replace(" ", "_"), value=h["label"], source_ref="invoice", tier=1),
                        EvidenceIn(source="vendor_master", field="linked_vendor", value=h["other_vendor"], source_ref=f"graph:vendor:{h['other_vendor_id']}",
                                   excerpt=f"{h['other_vendor']} — {h['kind']} {h['label']}" + (f" (seen in case {h['case_id']})" if h.get("case_id") else ""), tier=1),
                    ],
                    confidence=0.9, severity="warn", assertion={"op": "info"},
                    data={"observed": h["label"], "baseline": h["other_vendor"], "label_observed": h["kind"].capitalize(), "label_baseline": "Also used by"},
                ))
                claims.append(c.id)

        # --- GST: checksum and format only. There is no registry provider, so registry status is never claimed.
        if inv_gstin:
            ctx.progress(AGENT, f"Checking GSTIN {inv_gstin} format and checksum")
            fmt_ok = valid_gstin(inv_gstin)
            checks["gst_format"] = {"status": "passed" if fmt_ok else "fired", "reason": "valid GSTIN format and checksum" if fmt_ok else "GSTIN fails the format/checksum test"}
            checks["gst_registry"] = ctx.unverifiable(AGENT, "gst_registry", lookups.GST_REGISTRY_REASON)
            manual = (v.gst_manual or None) if v is not None else None
            if manual and str(manual.get("gstin", "")).upper() == inv_gstin.upper():
                who, when = manual.get("entered_by_name") or "a user", str(manual.get("entered_at", ""))[:10]
                status = manual.get("status")
                excerpt = (f"GSTIN: {inv_gstin}\nLegal Name: {manual.get('legal_name') or '-'}\nStatus: {status}\n"
                           f"Entered manually by {who} on {when} (not verified against the GST registry)")
                c = record_claim(s, ctx, AGENT, AgentClaim(
                    claim=f"GST status for {inv_gstin} was entered manually by {who} on {when}: {status}. This is not a registry verification.",
                    evidence=[EvidenceIn(source="vendor_master", field="gst_manual_status", value=status, source_ref=f"vendor:{v.id}:gst_manual", excerpt=excerpt, tier=2)],
                    confidence=0.6, severity="info" if status == "Active" else "warn",
                    assertion={"op": "quote", "evidence": "$0", "quote": f"Status: {status}"},
                    data={"observed": status, "baseline": "Active", "label_observed": "GST status (entered manually)", "label_baseline": "Expected",
                          "manual": True, "entered_by": who, "entered_at": when},
                ))
                claims.append(c.id)
                checks["gst_manual"] = {"status": "passed" if status == "Active" else "fired", "reason": f"{status} — entered manually by {who} on {when}", "manual": True}

        # --- domain: a domain already verified for this vendor passes without a lookup; otherwise ask RDAP.
        if domain:
            verified_domains = [x.domain for x in b["domains"] if x.verified]
            if domain in verified_domains:
                checks["domain_verification"] = {"status": "passed", "reason": "verified domain for this vendor", "signal": "new_domain"}
            else:
                ctx.progress(AGENT, f"Looking up registration date of {domain} (RDAP)")
                who = lookups.rdap_lookup(domain, ctx.budget)
                if who.status != "ok" or who.created is None:
                    checks["domain_verification"] = {**ctx.unverifiable(AGENT, "domain_verification", who.reason or "registration date unavailable"), "signal": "new_domain"}
                else:
                    sources += 1
                    age = (date.today() - who.created).days
                    res = d.new_domain(domain, age, verified_domains)
                    checks["domain_verification"] = {"status": "fired" if res.fired else "passed", "reason": f"registered {age} days ago", "signal": res.signal}
                    if res.fired:
                        quote = f"Creation Date: {who.created.isoformat()}"
                        ev = [
                            EvidenceIn(source="invoice", field="sender_domain", value=domain, source_ref="invoice", excerpt=fsnip(ex, "sender_domain"), tier=1),
                            EvidenceIn(source="domain", field="creation_date", value=who.created.isoformat(), source_ref=who.source_ref, excerpt=who.excerpt, tier=1),
                        ]
                        if verified_domains:
                            ev.append(EvidenceIn(source="vendor_master", field="verified_domains", value=verified_domains, source_ref=f"vendor:{v.id}" if v else "vendor_master", tier=1))
                        c = record_claim(s, ctx, AGENT, AgentClaim(
                            claim=f"Sender domain {domain} was registered {age} days ago" + (f" and is not a verified domain for this vendor (verified: {', '.join(verified_domains)})." if verified_domains else "."),
                            signal="new_domain",
                            evidence=ev,
                            confidence=0.95, severity=res.severity,
                            assertion={"op": "age_below", "evidence": "$1", "quote": quote, "max_days": 90, "claimed_days": age},
                            data={"observed": f"{domain} · registered {age} days ago", "baseline": ", ".join(verified_domains) or None, "domain": domain,
                                  "label_observed": "Sender domain", "label_baseline": "Verified domain"},
                        ))
                        claims.append(c.id)

    ctx.emit("agent.completed", agent=AGENT, status="done", message=f"Entity match: {entity}")
    return {"checks": checks, "entity_match": entity, "sources": sources}
