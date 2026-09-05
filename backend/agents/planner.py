"""Understand the learner, then plan the lesson."""
from __future__ import annotations

import json
import logging
from typing import Any

from backend.agents.pedagogy import Level, TimePlan, budget
from backend.core import prompts
from backend.core.llm import complete_json
from backend.rag.retriever import pack_context, retrieve_multi
from backend.rag.vector_store import VectorStore

log = logging.getLogger(__name__)

LANGUAGE_NAMES = {
    "en": "English", "hi": "Hindi", "hinglish": "Hinglish", "bn": "Bengali",
    "ta": "Tamil", "te": "Telugu", "mr": "Marathi", "gu": "Gujarati",
    "kn": "Kannada", "ml": "Malayalam", "pa": "Punjabi", "or": "Odia",
    "as": "Assamese", "ur": "Urdu", "es": "Spanish", "fr": "French",
    "de": "German", "ar": "Arabic", "zh": "Chinese", "ja": "Japanese",
    "pt": "Portuguese", "ru": "Russian", "id": "Indonesian", "sw": "Swahili",
}


def build_profile(request_text: str, *, defaults: dict | None = None) -> dict:
    """Parse a free-text request like:
    'I am a beginner. Teach me Chapter 4 in 20 minutes in Hindi with simple
    examples. Ask me questions during the lesson and test me at the end.'
    """
    defaults = defaults or {}
    profile = complete_json(prompts.PROFILER, f"Student request:\n{request_text}", temperature=0.1)

    profile.setdefault("level", defaults.get("level", "beginner"))
    profile.setdefault("teaching_language", defaults.get("language", "en"))
    profile.setdefault("minutes", defaults.get("minutes", 20))
    profile.setdefault("depth", "standard")
    profile.setdefault("goal", "curiosity")

    lang = str(profile["teaching_language"]).lower()
    profile["teaching_language"] = lang
    profile["language_label"] = profile.get("language_label") or LANGUAGE_NAMES.get(lang, lang)

    try:
        profile["minutes"] = max(2.0, float(profile["minutes"]))
    except (TypeError, ValueError):
        profile["minutes"] = 20.0
    if profile["level"] not in {l.value for l in Level}:
        profile["level"] = "beginner"
    return profile


def plan_lesson(
    profile: dict,
    *,
    doc_id: str | None = None,
    section: str | None = None,
) -> dict:
    """Produce the lesson plan: ordered concepts, depth, seconds and checkpoints."""
    plan_budget: TimePlan = budget(float(profile["minutes"]))
    topic = profile.get("topic") or "the uploaded material"
    scope = section or profile.get("scope_hint")

    context = "(no source material — teach from general knowledge)"
    hits: list = []
    outline_note = ""

    if doc_id:
        store = VectorStore.load(doc_id)
        sections = store.sections()
        if sections:
            outline_note = "\nDOCUMENT OUTLINE:\n" + "\n".join(
                f"- {s['label']} ({s['chunks']} passages)" for s in sections[:40]
            )
        queries = _plan_queries(topic, scope)
        hits = retrieve_multi(doc_id, queries, per_query=5, limit=18)
        if scope:
            scoped = store.search(scope, top_k=10, section=scope)
            seen = {h.chunk.id for h in hits}
            hits = [h for h in scoped if h.chunk.id not in seen] + hits
        context = pack_context(hits, max_tokens=4000)

    user = f"""LEARNER PROFILE
{json.dumps(profile, ensure_ascii=False, indent=2)}

TIME BUDGET
Total: {plan_budget.total_seconds}s ({profile['minutes']} minutes), mode "{plan_budget.mode}"
Teaching time to allocate across concepts: {plan_budget.teaching_s}s
Aim for about {plan_budget.concept_count} concepts and {plan_budget.checkpoint_count} checkpoints.

SCOPE
{scope or "the whole topic"}
{outline_note}

SOURCE MATERIAL
{context}
"""
    plan = complete_json(prompts.PLANNER, user, temperature=0.3)
    return _normalise_plan(plan, plan_budget, profile, hits)


def _plan_queries(topic: str, scope: str | None) -> list[str]:
    """Fan out into sub-queries so the planner sees the breadth of a chapter."""
    base = scope or topic
    return [
        base,
        f"{base} definition",
        f"{base} key concepts",
        f"{base} formula OR equation OR rule",
        f"{base} example OR problem",
        f"{base} summary OR conclusion",
        f"{base} diagram OR figure",
    ]


def _normalise_plan(plan: dict, tb: TimePlan, profile: dict, hits: list) -> dict:
    """Repair anything the model got structurally wrong, and rescale time.

    Models routinely over-allocate seconds. Rather than reject the plan, rescale
    proportionally to the real budget — the ordering and relative emphasis are
    the valuable part, the absolute seconds are not.
    """
    concepts: list[dict[str, Any]] = plan.get("concepts") or []
    if not concepts:
        concepts = [{
            "key": "overview",
            "name": plan.get("title") or profile.get("topic", "Topic"),
            "objective": "Understand the main idea",
            "depth": "explain",
            "seconds": tb.teaching_s,
            "difficulty": "medium",
        }]

    seen_keys: set[str] = set()
    cleaned: list[dict] = []
    for i, c in enumerate(concepts):
        key = str(c.get("key") or f"concept_{i+1}").strip().lower().replace(" ", "_")
        while key in seen_keys:
            key = f"{key}_{i}"
        seen_keys.add(key)
        try:
            seconds = float(c.get("seconds") or 0)
        except (TypeError, ValueError):
            seconds = 0.0
        cleaned.append({
            "key": key,
            "name": c.get("name") or key.replace("_", " ").title(),
            "objective": c.get("objective", ""),
            "depth": c.get("depth") if c.get("depth") in {"mention", "explain", "master"} else "explain",
            "seconds": seconds,
            "difficulty": c.get("difficulty") if c.get("difficulty") in {"easy", "medium", "hard"} else "medium",
            "prerequisites": [p for p in (c.get("prerequisites") or []) if isinstance(p, str)],
            "common_errors": [e for e in (c.get("common_errors") or []) if isinstance(e, str)],
            "source_chunks": [s for s in (c.get("source_chunks") or []) if isinstance(s, str)],
            "checkpoint": bool(c.get("checkpoint", True)),
            "order": i,
        })

    total = sum(c["seconds"] for c in cleaned)
    if total <= 0:
        share = tb.teaching_s / len(cleaned)
        for c in cleaned:
            c["seconds"] = share
    else:
        scale = tb.teaching_s / total
        for c in cleaned:
            c["seconds"] = max(20.0, round(c["seconds"] * scale, 1))

    cleaned = _topological_order(cleaned)

    # Guarantee at least one checkpoint whenever there is time for it.
    if tb.checkpoint_count and not any(c["checkpoint"] for c in cleaned):
        cleaned[-1]["checkpoint"] = True

    return {
        "title": plan.get("title") or profile.get("topic", "Lesson"),
        "summary": plan.get("summary", ""),
        "subject": plan.get("subject", "general"),
        "assumed_prerequisites": plan.get("assumed_prerequisites", []),
        "gaps": plan.get("gaps", []),
        "concepts": cleaned,
        "time_plan": {
            "mode": tb.mode,
            "total_seconds": tb.total_seconds,
            "intro_s": tb.intro_s,
            "teaching_s": tb.teaching_s,
            "checks_s": tb.checks_s,
            "assessment_s": tb.assessment_s,
            "recap_s": tb.recap_s,
        },
        "grounded_in": sorted({h.chunk.id for h in hits}),
        "source_labels": {h.chunk.id: h.chunk.label for h in hits},
    }


def _topological_order(concepts: list[dict]) -> list[dict]:
    """Reorder so prerequisites always precede dependants. Cycles keep the
    model's original order rather than failing the lesson."""
    by_key = {c["key"]: c for c in concepts}
    visited: dict[str, int] = {}
    out: list[dict] = []

    def visit(key: str, stack: set[str]) -> None:
        if visited.get(key) == 2 or key in stack:
            return
        stack.add(key)
        for prereq in by_key.get(key, {}).get("prerequisites", []):
            if prereq in by_key:
                visit(prereq, stack)
        stack.discard(key)
        if visited.get(key) != 2:
            visited[key] = 2
            out.append(by_key[key])

    for c in concepts:
        visit(c["key"], set())
    for i, c in enumerate(out):
        c["order"] = i
    return out or concepts
