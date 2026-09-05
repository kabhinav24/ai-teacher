"""Questioning, grading and reporting."""
from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher

from backend.agents.pedagogy import MISCONCEPTION_PLAYBOOK, ConceptState
from backend.core import prompts
from backend.core.llm import complete_json

log = logging.getLogger(__name__)

VALID_TAGS = set(MISCONCEPTION_PLAYBOOK.keys())
_NO_ANSWER = re.compile(r"^\s*(idk|dk|dunno|don'?t know|no idea|pata nahi|nahi pata|malum nahi|skip|pass|\?+|-+)\s*$", re.I)


def make_question(
    concept: dict,
    *,
    profile: dict,
    difficulty: str,
    kind: str | None = None,
    seconds: int = 30,
    asked_before: list[str] | None = None,
) -> dict:
    lang = profile.get("teaching_language", "en")
    user = f"""CONCEPT JUST TAUGHT
{json.dumps({k: concept.get(k) for k in ('key','name','objective','common_errors')}, ensure_ascii=False, indent=2)}

LEVEL: {profile.get('level')}
DIFFICULTY: {difficulty}
PREFERRED TYPE: {kind or 'choose what best tests this concept'}
TEACHING LANGUAGE: {profile.get('language_label', lang)} ({lang})
TIME TO ANSWER: {seconds} seconds
ALREADY ASKED (do not repeat): {asked_before or 'none'}
"""
    q = complete_json(prompts.QUESTIONER, user, temperature=0.6)
    return _normalise_question(q, concept, difficulty)


def make_assessment(plan: dict, states: dict[str, ConceptState], *, profile: dict, count: int = 5) -> list[dict]:
    lang = profile.get("teaching_language", "en")
    perf = [
        {
            "concept": c["name"],
            "key": c["key"],
            "objective": c.get("objective", ""),
            "mastery": round(states[c["key"]].p_known, 2) if c["key"] in states else None,
            "misconceptions": states[c["key"]].misconceptions if c["key"] in states else [],
        }
        for c in plan["concepts"]
    ]
    user = f"""LESSON: {plan.get('title')}
NUMBER OF QUESTIONS: {count}
LEVEL: {profile.get('level')}
TEACHING LANGUAGE: {profile.get('language_label', lang)} ({lang})

PER-CONCEPT PERFORMANCE
{json.dumps(perf, ensure_ascii=False, indent=2)}
"""
    result = complete_json(prompts.ASSESSOR, user, temperature=0.5)
    questions = result.get("questions", []) if isinstance(result, dict) else result
    by_key = {c["key"]: c for c in plan["concepts"]}
    out = []
    for q in (questions or [])[:count]:
        concept = by_key.get(q.get("targets_concept"), plan["concepts"][0])
        out.append(_normalise_question(q, concept, q.get("difficulty", "medium")))
    return out


def grade(question: dict, answer: str, *, profile: dict, concept: dict) -> dict:
    """Grade a response. Deterministic paths first, LLM only where judgement is needed.

    MCQs and clear non-answers do not need a model call — spending one there
    adds latency and a chance of the model overriding a fact it can see.
    """
    answer = (answer or "").strip()

    if _NO_ANSWER.match(answer) or not answer:
        return {
            "verdict": "incorrect",
            "confidence": 1.0,
            "misconception_tag": "no_attempt",
            "what_they_got_right": None,
            "diagnosis": "Student did not attempt the question.",
            "feedback": _no_attempt_feedback(profile.get("teaching_language", "en"), question.get("hint")),
            "reveal_answer": False,
            "graded_by": "rule",
        }

    if question.get("type") == "mcq" and question.get("options"):
        picked = _match_option(answer, question["options"])
        if picked is not None:
            correct = picked == question.get("correct_option")
            if correct:
                return {
                    "verdict": "correct", "confidence": 1.0, "misconception_tag": None,
                    "what_they_got_right": question.get("expected_answer"),
                    "diagnosis": "Selected the correct option.",
                    "feedback": _praise(profile.get("teaching_language", "en")),
                    "reveal_answer": True, "graded_by": "rule",
                }
            # Wrong option chosen: ask the model *why*, not *whether*.
            return _llm_grade(question, answer, profile, concept, hint=f"The student chose option '{picked}', which is wrong.")

    if question.get("type") == "numeric":
        verdict = _numeric_match(answer, question.get("expected_answer", ""))
        if verdict is True:
            return {
                "verdict": "correct", "confidence": 0.95, "misconception_tag": None,
                "what_they_got_right": "the numeric result",
                "diagnosis": "Correct value.", "feedback": _praise(profile.get("teaching_language", "en")),
                "reveal_answer": True, "graded_by": "rule",
            }

    return _llm_grade(question, answer, profile, concept)


def _llm_grade(question: dict, answer: str, profile: dict, concept: dict, *, hint: str = "") -> dict:
    lang = profile.get("teaching_language", "en")
    user = f"""QUESTION ASKED: {question.get('prompt')}
QUESTION TYPE: {question.get('type')}
OPTIONS: {json.dumps(question.get('options', []), ensure_ascii=False)}
MODEL ANSWER: {question.get('expected_answer')}
ALSO ACCEPTABLE: {question.get('accepts', [])}
MISCONCEPTION THIS QUESTION PROBES: {question.get('probes_misconception')}

CONCEPT: {concept.get('name')} — {concept.get('objective','')}
KNOWN COMMON ERRORS: {concept.get('common_errors', [])}

STUDENT'S ANSWER: {answer}
{hint}

LEVEL: {profile.get('level')}
TEACHING LANGUAGE: {profile.get('language_label', lang)} ({lang})
"""
    result = complete_json(prompts.EVALUATOR, user, temperature=0.2)

    verdict = result.get("verdict")
    if verdict not in {"correct", "partially_correct", "incorrect"}:
        result["verdict"] = "incorrect"
    tag = result.get("misconception_tag")
    if result["verdict"] == "correct":
        result["misconception_tag"] = None
    elif tag not in VALID_TAGS:
        result["misconception_tag"] = "unknown"
    try:
        result["confidence"] = min(1.0, max(0.0, float(result.get("confidence", 0.7))))
    except (TypeError, ValueError):
        result["confidence"] = 0.7
    result["graded_by"] = "llm"
    return result


def build_report(plan: dict, states: dict[str, ConceptState], transcript: list[dict], *, profile: dict, score: float) -> dict:
    lang = profile.get("teaching_language", "en")
    by_key = {c["key"]: c for c in plan["concepts"]}
    detail = [
        {
            "concept": by_key.get(k, {}).get("name", k),
            "mastery": round(s.p_known, 2),
            "attempts": s.attempts,
            "accuracy": round(s.accuracy, 2),
            "misconceptions": s.misconceptions,
            "strategies_needed": [x.value for x in s.strategies_used],
        }
        for k, s in states.items()
    ]
    answers = [
        {"q": t.get("question"), "answer": t.get("answer"), "verdict": t.get("verdict"), "tag": t.get("misconception_tag")}
        for t in transcript if t.get("kind") == "answer"
    ]
    user = f"""LESSON: {plan.get('title')} ({plan.get('subject')})
OVERALL SCORE: {score}%
LEVEL: {profile.get('level')}
TEACHING LANGUAGE: {profile.get('language_label', lang)} ({lang})

PER-CONCEPT STATE
{json.dumps(detail, ensure_ascii=False, indent=2)}

EVERY ANSWER GIVEN
{json.dumps(answers, ensure_ascii=False, indent=2)}
"""
    report = complete_json(prompts.REPORTER, user, temperature=0.4)
    report["score"] = score
    report["concept_detail"] = detail
    return report


# ---------------------------------------------------------------- helpers

def _normalise_question(q: dict, concept: dict, difficulty: str) -> dict:
    qtype = q.get("type") if q.get("type") in {"mcq", "short_answer", "numeric", "explain_back", "predict"} else "short_answer"
    options = q.get("options") or []
    if qtype != "mcq":
        options = []
    else:
        options = [
            {"id": str(o.get("id") or chr(97 + i)), "text": str(o.get("text", ""))}
            for i, o in enumerate(options) if isinstance(o, dict)
        ]
        if len(options) < 2:
            qtype, options = "short_answer", []
    return {
        "type": qtype,
        "prompt": q.get("prompt", f"What did you understand about {concept.get('name')}?"),
        "options": options,
        "correct_option": q.get("correct_option") if qtype == "mcq" else None,
        "expected_answer": q.get("expected_answer", ""),
        "accepts": q.get("accepts", []),
        "targets_concept": q.get("targets_concept") or concept.get("key"),
        "probes_misconception": q.get("probes_misconception"),
        "hint": q.get("hint"),
        "difficulty": difficulty if difficulty in {"easy", "medium", "hard"} else "medium",
    }


def _match_option(answer: str, options: list[dict]) -> str | None:
    """Accept 'b', 'B)', 'option b', or the option text itself."""
    a = answer.strip().lower().rstrip(").:")
    for o in options:
        if a == str(o["id"]).lower():
            return str(o["id"])
    m = re.match(r"^(?:option\s+)?([a-d])\b", a)
    if m:
        for o in options:
            if str(o["id"]).lower() == m.group(1):
                return str(o["id"])
    best, best_score = None, 0.0
    for o in options:
        score = SequenceMatcher(None, a, str(o["text"]).lower()).ratio()
        if score > best_score:
            best, best_score = str(o["id"]), score
    return best if best_score > 0.75 else None


def _numeric_match(answer: str, expected: str) -> bool | None:
    nums_a = re.findall(r"-?\d+\.?\d*", answer.replace(",", ""))
    nums_e = re.findall(r"-?\d+\.?\d*", str(expected).replace(",", ""))
    if not nums_a or not nums_e:
        return None
    try:
        got, want = float(nums_a[-1]), float(nums_e[-1])
    except ValueError:
        return None
    tol = max(abs(want) * 0.02, 1e-6)
    return abs(got - want) <= tol


def _praise(lang: str) -> str:
    return {
        "hi": "बिलकुल सही। आगे बढ़ते हैं।",
        "hinglish": "Ekdum sahi! Chalo aage badhte hain.",
    }.get(lang, "That's right. Let's keep going.")


def _no_attempt_feedback(lang: str, hint: str | None) -> str:
    tail = f" {hint}" if hint else ""
    return {
        "hi": f"कोई बात नहीं, एक संकेत देता हूँ।{tail}",
        "hinglish": f"Koi baat nahi, ek hint deta hoon.{tail}",
    }.get(lang, f"No problem — here's a hint.{tail}")
