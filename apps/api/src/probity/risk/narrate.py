"""Plain-language explanation of an invoice risk score (risk/invoice_scoring.py).

Code decides, the LLM only rephrases. The model receives the deterministic "explanation" block as read-only data
and returns prose; that prose is accepted only if every number in it already appears in the block and it names
the tier the engine chose. Anything else (invalid output, an invented figure, an API error, no API key) falls
back to the engine's own template sentence, so an explanation is always available and always consistent with
the score. The narrative has no field that could carry a score, tier or action back.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field

from probity.guardrails.text import neutralize_language, wrap_untrusted

AGENT = "risk_explainer"
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


class ScoreNarrative(BaseModel):
    summary: str = Field(max_length=700, description="2-3 sentences: the score, the tier, and the main reasons")
    key_points: list[str] = Field(default_factory=list, max_length=4, description="one short line per main driver or caveat")


def _numbers(text: str) -> set[Decimal]:
    out = set()
    for m in _NUM.finditer(text):
        try:
            out.add(Decimal(m.group(0).rstrip(",").replace(",", "")).normalize())
        except InvalidOperation:
            continue
    return out


def facts(result: dict) -> tuple[dict, dict]:
    """(engine facts, untrusted evidence) handed to the model. Evidence quotes invoice text, so it is kept apart."""
    exp = result.get("explanation") or {}
    contribs = exp.get("contributions") or []
    engine = {
        "final_score": result.get("final_score"),
        "tier": result.get("tier"),
        "recommended_action": result.get("recommended_action"),
        "coverage": result.get("coverage"),
        "tier_reason": exp.get("tier_reason"),
        "escalations": exp.get("escalations") or [],
        "drivers": [{"signal": c["label"], "share_of_score": f"{round(c['share'] * 100)}%", "signal_score": c["score"]}
                    for c in contribs if c["signal"] in (exp.get("top_drivers") or [])],
        "clear_signals": [c["label"] for c in contribs if c["contribution"] == 0],
        "not_evaluated": [u["label"] for u in exp.get("unknown") or []],
        "low_confidence": "low_confidence" in (result.get("warnings") or []),
        "engine_sentence": exp.get("text"),
    }
    evidence = {c["label"]: c["evidence"] for c in contribs if c["signal"] in (exp.get("top_drivers") or [])}
    evidence.update({u["label"]: u["reason"] for u in exp.get("unknown") or []})
    return engine, evidence


def check(out: ScoreNarrative, result: dict) -> list[str]:
    """Reasons to reject the model's text; empty means it is consistent with the engine output."""
    engine, evidence = facts(result)
    source = json.dumps(engine, ensure_ascii=False) + " " + " ".join(evidence.values())
    allowed = _numbers(source) | {Decimal(n) for n in range(0, len(result.get("signals") or []) + 1)}
    text = " ".join([out.summary, *out.key_points])
    problems = []
    invented = sorted(str(n) for n in _numbers(text) - allowed)
    if invented:
        problems.append(f"numbers not in engine output: {', '.join(invented[:5])}")
    tier = result.get("tier")
    if tier and not re.search(rf"(?i)\b{re.escape(str(tier))}\b", out.summary):
        problems.append(f"summary does not state the tier {tier}")
    if not out.summary.strip():
        problems.append("empty summary")
    return problems


def _template(result: dict) -> ScoreNarrative:
    exp = result.get("explanation") or {}
    points = [f"{c['label'][:1].upper() + c['label'][1:]}: {c['evidence']}" for c in exp.get("contributions") or []
              if c["signal"] in (exp.get("top_drivers") or [])]
    points += [f"Not evaluated - {u['label']}: {u['reason']}" for u in exp.get("unknown") or []]
    return ScoreNarrative(summary=exp.get("text") or "No explanation available.", key_points=points[:4])


def narrate(result: dict, *, tags: dict | None = None, budget=None) -> dict:  # type: ignore[no-untyped-def]
    """Return {summary, key_points, source: "llm" | "template", note}. Never raises for LLM problems."""
    from probity.llm import client as llm

    template = _template(result)
    engine, evidence = facts(result)
    user = ("Risk engine output (authoritative, read-only):\n" + json.dumps(engine, ensure_ascii=False, indent=1)
            + "\n\nEvidence for each signal:\n" + wrap_untrusted("invoice_evidence", json.dumps(evidence, ensure_ascii=False, indent=1)))
    try:
        pv, system = llm.load_prompt(AGENT, "summary")
        out = llm.generate(schema=ScoreNarrative, system=system, user=user, tier="fast",
                           tags={**(tags or {}), "agent": AGENT, "prompt_version": pv}, mock=lambda: template, budget=budget)
    except Exception as e:  # noqa: BLE001 - the deterministic sentence is always a valid explanation
        return {**template.model_dump(), "source": "template", "note": f"LLM unavailable ({type(e).__name__})"}
    if out == template:  # mock mode, or a cached miss that fell back to the template
        return {**template.model_dump(), "source": "template", "note": None}
    problems = check(out, result)
    if problems:
        return {**template.model_dump(), "source": "template", "note": "LLM text rejected: " + "; ".join(problems)}
    return {"summary": neutralize_language(out.summary), "key_points": [neutralize_language(p) for p in out.key_points],
            "source": "llm", "note": None}
