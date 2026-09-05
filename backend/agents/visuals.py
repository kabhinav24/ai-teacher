"""Choosing the right visual for the subject.

The brief asks participants to demonstrate *how* the system decides. It works in
two stages:

1. A deterministic prior from the subject and the text of the concept. This is
   inspectable and testable — `explain_choice()` returns the evidence.
2. The LLM director picks the final renderer, but only from the shortlist the
   prior allows. That stops the common failure where every concept, in every
   subject, becomes a bullet list.
"""
from __future__ import annotations

import logging
import re

from backend.core import prompts
from backend.core.llm import complete_json

log = logging.getLogger(__name__)

RENDERERS = {"equation", "plot", "diagram", "flow", "timeline", "table", "code", "map", "bullets"}

#: Renderers each subject may use, most-preferred first.
SUBJECT_PRIORS: dict[str, list[str]] = {
    "mathematics": ["equation", "plot", "table", "diagram", "flow"],
    "physics": ["diagram", "equation", "plot", "flow", "table"],
    "chemistry": ["diagram", "equation", "flow", "table"],
    "biology": ["diagram", "flow", "table", "timeline"],
    "computer_science": ["code", "flow", "diagram", "table", "plot"],
    "programming": ["code", "flow", "diagram", "table"],
    "history": ["timeline", "map", "table", "diagram"],
    "geography": ["map", "diagram", "table", "plot"],
    "economics": ["plot", "table", "flow", "equation"],
    "language": ["table", "diagram", "bullets"],
    "general": ["diagram", "table", "flow", "timeline", "plot", "bullets"],
}

#: Textual triggers that override the subject prior. A history lesson that hits
#: a formula should still get an equation.
TEXT_SIGNALS: list[tuple[str, str]] = [
    (r"\b(equation|formula|derive|derivation|solve for|integral|derivative|theorem|proof)\b|[=∫∑√]|\^2", "equation"),
    (r"\b(graph|curve|plot|versus|vs\.?|trend|distribution|proportional|rate of change)\b", "plot"),
    (r"\b(circuit|diagram|structure|apparatus|anatomy|cross-section|labell?ed|parts of|forces?)\b", "diagram"),
    (r"\b(step|steps|process|algorithm|procedure|workflow|pipeline|lifecycle|then|sequence)\b", "flow"),
    (r"\b(\d{3,4}\s?(bc|ad|ce|bce)|century|era|timeline|chronolog|dynasty|empire|treaty|revolt|revolution|war of)\b", "timeline"),
    # Three or more distinct years in the same passage is the strongest
    # timeline signal there is, and bare years are how textbooks actually
    # write dates.
    (r"(?:\b(?:1[0-9]{3}|20[0-2][0-9])\b.*?){3}", "timeline"),
    (r"\b(compare|comparison|difference between|versus|advantages and disadvantages|types of|classif)\b", "table"),
    (r"\b(code|function|class|syntax|compile|loop|array|variable|api|def |import |for\s*\()\b", "code"),
    (r"\b(map|region|continent|latitude|border|located|geograph|river|plateau)\b", "map"),
]


def choose_visual(concept: dict, narration: str, *, subject: str, profile: dict) -> dict:
    """Return {renderer, reason, spec, decision_trace}."""
    shortlist, trace = shortlist_renderers(concept, narration, subject=subject)

    user = f"""SUBJECT: {subject}
CONCEPT: {concept.get('name')} — {concept.get('objective','')}
TEACHING LANGUAGE: {profile.get('language_label', profile.get('teaching_language','en'))}
LEVEL: {profile.get('level')}

NARRATION THAT THIS VISUAL ACCOMPANIES:
{narration[:1800]}

ALLOWED RENDERERS (choose exactly one, in preference order): {shortlist}
"""
    try:
        result = complete_json(prompts.VISUAL_DIRECTOR, user, temperature=0.3)
    except Exception as exc:  # noqa: BLE001
        log.warning("Visual director failed (%s); using deterministic fallback.", exc)
        result = {}

    renderer = result.get("renderer")
    if renderer not in shortlist:
        # Model went outside the shortlist — respect the subject prior instead.
        log.info("Director chose '%s' outside shortlist %s; overriding.", renderer, shortlist)
        renderer = shortlist[0]
        result = {"renderer": renderer, "reason": "subject prior", "spec": result.get("spec", {})}

    spec = result.get("spec") if isinstance(result.get("spec"), dict) else {}
    spec = _repair_spec(renderer, spec, concept, narration)

    return {
        "renderer": renderer,
        "reason": result.get("reason", ""),
        "spec": spec,
        "decision_trace": trace,
    }


def shortlist_renderers(concept: dict, narration: str, *, subject: str) -> tuple[list[str], dict]:
    """Deterministic stage. Returns (shortlist, trace) for inspection."""
    haystack = " ".join([
        str(concept.get("name", "")),
        str(concept.get("objective", "")),
        " ".join(concept.get("common_errors", []) or []),
        narration[:2500],
    ]).lower()

    scores: dict[str, float] = {}
    prior = SUBJECT_PRIORS.get(subject, SUBJECT_PRIORS["general"])
    for i, r in enumerate(prior):
        scores[r] = scores.get(r, 0.0) + (len(prior) - i) * 1.0

    matched: list[dict] = []
    for pattern, renderer in TEXT_SIGNALS:
        found = re.findall(pattern, haystack, re.IGNORECASE)
        if found:
            weight = 2.5 * min(len(found), 4)
            scores[renderer] = scores.get(renderer, 0.0) + weight
            matched.append({"renderer": renderer, "weight": weight, "evidence": str(found[:3])})

    # Bullets are last resort: only reachable when nothing else scored.
    scores["bullets"] = scores.get("bullets", 0.0) * 0.25

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    shortlist = [r for r, s in ranked if s > 0][:4] or ["bullets"]
    if "bullets" not in shortlist:
        shortlist.append("bullets")

    return shortlist, {
        "subject": subject,
        "subject_prior": prior,
        "text_signals": matched,
        "scores": {k: round(v, 2) for k, v in ranked},
        "shortlist": shortlist,
    }


def _repair_spec(renderer: str, spec: dict, concept: dict, narration: str) -> dict:
    """Guarantee a renderable spec even when the model returns a partial one."""
    fallback_points = _key_lines(narration) or [concept.get("objective") or concept.get("name", "")]

    if renderer == "equation" and not spec.get("lines"):
        return {"lines": _find_equations(narration) or [concept.get("name", "")], "caption": concept.get("name", "")}
    if renderer == "plot" and not (spec.get("expression") or spec.get("series")):
        return {"kind": "function", "expression": "x", "x_range": [-5, 5], "x_label": "x", "y_label": "y"}
    if renderer == "diagram" and not spec.get("nodes"):
        nodes = [
            {"id": f"n{i}", "label": p[:34], "x": 0.2 + 0.3 * (i % 3), "y": 0.3 + 0.25 * (i // 3), "shape": "box"}
            for i, p in enumerate(fallback_points[:5])
        ]
        return {"nodes": nodes, "edges": [{"from": f"n{i}", "to": f"n{i+1}"} for i in range(len(nodes) - 1)]}
    if renderer == "flow" and not spec.get("steps"):
        steps = [{"id": f"s{i}", "label": p[:40], "kind": "process"} for i, p in enumerate(fallback_points[:5])]
        if steps:
            steps[0]["kind"] = "start"
            steps[-1]["kind"] = "end"
        return {"steps": steps, "edges": [{"from": f"s{i}", "to": f"s{i+1}"} for i in range(len(steps) - 1)]}
    if renderer == "timeline" and not spec.get("events"):
        return {"events": [{"when": "", "label": p[:60]} for p in fallback_points[:5]]}
    if renderer == "table" and not spec.get("rows"):
        return {"columns": ["Point", "Detail"], "rows": [[p[:28], p[28:88] or "—"] for p in fallback_points[:4]]}
    if renderer == "code" and not spec.get("code"):
        return {"language": "text", "code": "\n".join(fallback_points[:6]), "highlight_lines": []}
    if renderer == "map" and not spec.get("regions"):
        return {"regions": [{"name": p[:30], "note": ""} for p in fallback_points[:5]]}
    if renderer == "bullets" and not spec.get("points"):
        return {"points": fallback_points[:5]}
    return spec


def _key_lines(narration: str) -> list[str]:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?।])\s+", narration or "") if 20 < len(s.strip()) < 130]
    return sentences[:6]


def _find_equations(text: str) -> list[str]:
    return [m.strip() for m in re.findall(r"[A-Za-z0-9²³\)\]]\s*=\s*[^.,;\n]{2,60}", text or "")][:5]
