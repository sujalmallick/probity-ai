"""Agent 3 — Vendor Investigator: "Who is this entity; is it the same as claimed?"

A mismatch becomes a claim only if both sides are evidenced.
"""

from __future__ import annotations

from datetime import date

from probity import graph_rel
from probity.agents.common import fsnip, fv, load_case, record_claim, vendor_bundle
from probity.db.session import session_scope
from probity.events import CaseCtx
from probity.evidence.models import AgentClaim, EvidenceIn
from probity.signals import detectors as d
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
            checks["vendor_identity"] = {"status": "skipped", "reason": "unknown vendor — not in vendor master"}
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
            checks["vendor_identity"] = {"status": "fired" if res.fired else ("skipped" if res.skipped else "passed"), "reason": res.skipped or res.detail.get("field", ""), "signal": res.signal}
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

        # --- GST registry
        if inv_gstin:
            ctx.progress(AGENT, f"Looking up GSTIN {inv_gstin} in GST registry")
            rec = lookups.gst_lookup(inv_gstin, ctx.budget)
            sources += 1
            if rec:
                excerpt = f"GSTIN: {inv_gstin}\nLegal Name: {rec['legal_name']}\nStatus: {rec['status']}\nAddress: {rec['address']}"
                c = record_claim(s, ctx, AGENT, AgentClaim(
                    claim=f"GSTIN {inv_gstin} is registered to {rec['legal_name']} (status: {rec['status']}).",
                    evidence=[EvidenceIn(source="registry", field="gstin", value=inv_gstin, source_ref=rec["source_ref"], excerpt=excerpt, tier=1)],
                    confidence=0.95, severity="info", assertion={"op": "quote", "evidence": "$0", "quote": f"Status: {rec['status']}"},
                ))
                claims.append(c.id)
                checks["gst_registry"] = {"status": "passed" if rec["status"] == "Active" else "fired", "reason": rec["status"]}
            else:
                checks["gst_registry"] = {"status": "skipped", "reason": "GSTIN not found / registry unavailable"}

        # --- domain
        if domain:
            ctx.progress(AGENT, f"Checking domain registration for {domain}")
            verified_domains = [x.domain for x in b["domains"] if x.verified]
            who = lookups.whois(domain, ctx.budget)
            sources += 1
            age = (date.today() - who.created).days if who and who.created else None
            res = d.new_domain(domain, age, verified_domains)
            checks["domain_verification"] = {"status": "fired" if res.fired else ("skipped" if res.skipped else "passed"), "reason": res.skipped or (f"{age} days old" if age is not None else "verified domain"), "signal": res.signal}
            if res.fired and who:
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
