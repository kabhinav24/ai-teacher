"""The speaking agents: explain, re-explain, answer interruptions."""
from __future__ import annotations

import json
import logging

from backend.agents.pedagogy import (
    ConceptState,
    Level,
    Strategy,
    next_strategy,
    remediation_for,
    words_for_seconds,
)
from backend.core import prompts
from backend.core.llm import complete_json
from backend.rag.retriever import pack_context, retrieve, verify_grounding

log = logging.getLogger(__name__)

DEPTH_GUIDE = {
    "mention": "Name it and say why it matters. One or two sentences. Do not develop it.",
    "explain": "Develop the idea properly with one concrete example.",
    "master": "Develop it fully: the idea, why it is true, a worked example, and the boundary where it stops applying.",
}

LEVEL_GUIDE = {
    "beginner": "Assume no prior exposure. Everyday vocabulary, one idea per sentence, concrete before abstract. Introduce at most two technical terms.",
    "intermediate": "Assume the basics are in place. Use standard terminology, connect to related ideas, and include a practical example.",
    "advanced": "Assume fluency. Use precise technical language, state assumptions and edge cases, include the mathematics or implementation detail where it clarifies.",
}


def explain_concept(
    concept: dict,
    *,
    profile: dict,
    state: ConceptState,
    doc_id: str | None,
    previous_concept: str | None,
    strategy: Strategy | None = None,
) -> dict:
    """Generate the narration and board content for one lesson segment."""
    level = Level(profile.get("level", "beginner"))
    strategy = strategy or next_strategy(state, level)
    lang = profile.get("teaching_language", "en")
    seconds = float(concept.get("seconds", 60))
    words = words_for_seconds(seconds, lang)

    hits = []
    context = "(no source material — teach from general knowledge, stay accurate)"
    if doc_id:
        query = f"{concept['name']}. {concept.get('objective','')}"
        hits = retrieve(doc_id, query, top_k=6)
        context = pack_context(hits, max_tokens=2200)

    user = f"""CONCEPT TO TEACH
{json.dumps({k: concept.get(k) for k in ('key','name','objective','depth','difficulty','common_errors')}, ensure_ascii=False, indent=2)}

EXPLANATION STRATEGY: {strategy.value}
DEPTH: {DEPTH_GUIDE.get(concept.get('depth','explain'))}
LEVEL: {LEVEL_GUIDE.get(level.value)}
TEACHING LANGUAGE: {profile.get('language_label', lang)} ({lang})
STYLE NOTES: {profile.get('style_notes') or 'none'}
WORD BUDGET: about {words} words of narration ({seconds:.0f} seconds of speech)
PREVIOUSLY TAUGHT: {previous_concept or 'nothing yet — this opens the lesson'}
STRATEGIES ALREADY TRIED FOR THIS CONCEPT: {[s.value for s in state.strategies_used] or 'none'}

SOURCE MATERIAL
{context}
"""
    result = complete_json(prompts.EXPLAINER, user, temperature=0.55)
    state.strategies_used.append(strategy)

    narration = (result.get("narration") or "").strip()
    grounding = verify_grounding(narration, hits)
    result.update({
        "strategy": strategy.value,
        "concept_key": concept["key"],
        "target_seconds": seconds,
        "grounding": {
            "score": grounding.score,
            "passed": grounding.passed,
            "unsupported": grounding.unsupported[:3],
            "citations": grounding.citations_used,
        },
        "sources": [{"id": h.chunk.id, "label": h.chunk.label, "page": h.chunk.page} for h in hits[:5]],
    })
    return result


def remediate(
    concept: dict,
    *,
    profile: dict,
    state: ConceptState,
    misconception_tag: str,
    student_answer: str,
    question_prompt: str,
    doc_id: str | None,
) -> dict:
    """Re-teach after a wrong answer, using a strategy that has not failed yet."""
    level = Level(profile.get("level", "beginner"))
    strategy = next_strategy(state, level)
    fix = remediation_for(misconception_tag)
    lang = profile.get("teaching_language", "en")

    hits = []
    context = "(no source material)"
    if doc_id:
        hits = retrieve(doc_id, f"{concept['name']} {question_prompt}", top_k=4)
        context = pack_context(hits, max_tokens=1500)

    user = f"""CONCEPT: {concept['name']} — {concept.get('objective','')}

WHAT HAPPENED
Question asked: {question_prompt}
Student answered: {student_answer}

DIAGNOSED MISCONCEPTION: {misconception_tag} — {fix.label}
CORRECTIVE INSTRUCTION: {fix.instruction}

ASSIGNED STRATEGY: {strategy.value}
ALREADY TRIED (do not repeat these): {[s.value for s in state.strategies_used] or 'none'}
LEVEL: {LEVEL_GUIDE.get(level.value)}
TEACHING LANGUAGE: {profile.get('language_label', lang)} ({lang})
WORD BUDGET: about {words_for_seconds(45, lang)} words.

SOURCE MATERIAL
{context}
"""
    result = complete_json(prompts.REMEDIATOR, user, temperature=0.6)
    state.strategies_used.append(strategy)
    result.update({
        "strategy": strategy.value,
        "concept_key": concept["key"],
        "misconception_tag": misconception_tag,
        "misconception_label": fix.label,
        "target_seconds": 45,
    })
    return result


def answer_follow_up(
    question: str,
    *,
    profile: dict,
    plan: dict,
    current_concept_key: str | None,
    doc_id: str | None,
    recent_narration: str = "",
) -> dict:
    """Handle a student interrupting mid-lesson without losing lesson context."""
    lang = profile.get("teaching_language", "en")
    hits = []
    context = "(no source material)"
    if doc_id:
        hits = retrieve(doc_id, question, top_k=5)
        context = pack_context(hits, max_tokens=1800)

    taught = [c["name"] for c in plan["concepts"] if c["key"] == current_concept_key or c["order"] <= _order_of(plan, current_concept_key)]
    upcoming = [c["name"] for c in plan["concepts"] if c["order"] > _order_of(plan, current_concept_key)]

    user = f"""STUDENT'S QUESTION: {question}

LESSON STATE
Title: {plan.get('title')}
Currently teaching: {current_concept_key}
Already covered: {taught}
Still to come: {upcoming}
Last thing said: {recent_narration[-600:]}

TEACHING LANGUAGE: {profile.get('language_label', lang)} ({lang})

SOURCE MATERIAL
{context}
"""
    result = complete_json(prompts.FOLLOW_UP, user, temperature=0.4)
    result["sources"] = [{"id": h.chunk.id, "label": h.chunk.label} for h in hits[:4]]
    return result


def _order_of(plan: dict, key: str | None) -> int:
    for c in plan["concepts"]:
        if c["key"] == key:
            return c["order"]
    return -1
