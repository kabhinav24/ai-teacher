"""Schema-valid stub responses for LLM_PROVIDER=echo.

This exists so the whole pipeline — plan, explain, question, grade, render,
compose video — can be run and tested with no network and no API key. The
content is obviously synthetic; the *shapes* are exactly what the real agents
return, which is what CI needs to catch contract breaks.
"""
from __future__ import annotations

import json
import re


def stub_for(system: str, user: str) -> str:
    head = system[:400].lower()
    if "intake specialist" in head:
        return json.dumps(_profile(user))
    if "lesson planner" in head:
        return json.dumps(_plan(user))
    if "visual director" in head:
        return json.dumps(_visual(user))
    if "re-teaching" in head or "remediator" in head:
        return json.dumps(_remediation())
    if "voice of an ai teacher" in head:
        return json.dumps(_explanation(user))
    if "questioning module" in head:
        return json.dumps(_question(user))
    if "assessment module" in head:
        return json.dumps(_grade(user))
    if "end-of-lesson assessment" in head:
        return json.dumps({"questions": [_question(user) for _ in range(3)]})
    if "learning report" in head:
        return json.dumps(_report())
    if "learning path" in head:
        return json.dumps(_path(user))
    if "interruption" in head:
        return json.dumps(_follow_up())
    return json.dumps({"text": "stub response"})


def _minutes(user: str) -> float:
    m = re.search(r"([\d.]+)\s*minutes", user)
    return float(m.group(1)) if m else 20.0


def _profile(user: str) -> dict:
    lower = user.lower()
    lang = "hi" if "hindi" in lower else "hinglish" if "hinglish" in lower else "en"
    level = "advanced" if "advanced" in lower else "intermediate" if "intermediate" in lower else "beginner"
    return {
        "topic": "Sample Topic",
        "level": level,
        "teaching_language": lang,
        "language_label": {"hi": "Hindi", "hinglish": "Hinglish"}.get(lang, "English"),
        "minutes": _minutes(user) or 20,
        "goal": "curiosity",
        "depth": "standard",
        "style_notes": "simple examples",
        "scope_hint": None,
        "inferred_fields": ["goal"],
        "clarifying_question": None,
    }


def _plan(user: str) -> dict:
    total = 900.0
    m = re.search(r"across concepts:\s*(\d+)s", user)
    if m:
        total = float(m.group(1))
    names = ["Foundations", "The Core Idea", "Worked Example", "Where It Breaks"]
    per = total / len(names)
    return {
        "title": "Sample Lesson",
        "summary": "A short stub lesson used for offline runs.",
        "subject": "general",
        "assumed_prerequisites": ["basic arithmetic"],
        "gaps": [],
        "concepts": [
            {
                "key": f"concept_{i+1}",
                "name": n,
                "objective": f"Explain {n.lower()} in your own words",
                "depth": "explain",
                "seconds": per,
                "difficulty": "medium",
                "prerequisites": [f"concept_{i}"] if i else [],
                "common_errors": ["confusing this with the previous idea"],
                "source_chunks": ["c0"],
                "checkpoint": i % 2 == 1,
            }
            for i, n in enumerate(names)
        ],
    }


def _explanation(user: str) -> dict:
    return {
        "narration": (
            "Let's pick up where we left off. The idea here is simpler than it looks: "
            "when one quantity goes up and the other is held steady, the third one has to move. "
            "Think of it like water in a pipe. Widen the pipe and more water flows for the same push. "
            "Notice what happened there — nothing about the push changed, only the resistance did."
        ),
        "board_title": "The core idea",
        "board_points": ["One quantity held steady", "Widen the path", "Flow increases"],
        "key_terms": [{"term": "resistance", "gloss": "how much the path opposes flow"}],
        "citations": ["c0"],
        "callout": "Hold one thing fixed and the other two are locked together.",
    }


def _visual(user: str) -> dict:
    return {
        "renderer": "diagram" if "diagram" in user.lower() else "bullets",
        "reason": "shows the relationship between the parts at a glance",
        "spec": {
            "nodes": [
                {"id": "a", "label": "Source", "x": 0.2, "y": 0.6, "shape": "source"},
                {"id": "b", "label": "Path", "x": 0.5, "y": 0.6, "shape": "box"},
                {"id": "c", "label": "Result", "x": 0.8, "y": 0.6, "shape": "circle"},
            ],
            "edges": [{"from": "a", "to": "b", "label": "push"}, {"from": "b", "to": "c", "label": "flow"}],
            "caption": "Stub diagram",
        },
    }


def _question(user: str) -> dict:
    return {
        "type": "mcq",
        "prompt": "If the path gets narrower while the push stays the same, what happens to the flow?",
        "options": [
            {"id": "a", "text": "It increases"},
            {"id": "b", "text": "It decreases"},
            {"id": "c", "text": "It stays the same"},
        ],
        "correct_option": "b",
        "expected_answer": "It decreases",
        "accepts": ["goes down", "less flow", "kam ho jayega"],
        "targets_concept": "concept_1",
        "probes_misconception": "inverse_relationship",
        "hint": "Picture squeezing a hosepipe.",
        "difficulty": "medium",
    }


def _grade(user: str) -> dict:
    wrong = "increase" in user.lower().split("student's answer:")[-1]
    if wrong:
        return {
            "verdict": "incorrect",
            "confidence": 0.9,
            "misconception_tag": "inverse_relationship",
            "what_they_got_right": "you correctly spotted that the flow must change",
            "diagnosis": "Student has the direction of the relationship reversed.",
            "feedback": "You're right that the flow has to change — but check which way. Narrower path, less room to move.",
            "reveal_answer": False,
        }
    return {
        "verdict": "correct",
        "confidence": 0.95,
        "misconception_tag": None,
        "what_they_got_right": "the direction of the relationship",
        "diagnosis": "Correct reasoning.",
        "feedback": "Exactly right. Let's build on that.",
        "reveal_answer": True,
    }


def _remediation() -> dict:
    return {
        "narration": "Let's try this a different way. Forget the formula for a second and just squeeze a hosepipe in your head.",
        "board_title": "Try it from the other end",
        "board_points": ["Same push", "Narrower path", "Less gets through"],
        "strategy_used": "everyday_analogy",
        "follow_up_question": "Same push, half the width — more or less flow?",
        "expected_answer": "Less",
        "citations": ["c0"],
    }


def _report() -> dict:
    return {
        "headline": "Solid grasp of the basics, with one relationship still flipped.",
        "strong_areas": [{"concept": "Foundations", "evidence": "answered both checkpoint questions first time"}],
        "weak_areas": [{"concept": "The Core Idea", "evidence": "reversed the direction twice", "fix": "redo three problems saying the direction out loud first"}],
        "misconceptions_to_clear": ["When resistance goes up, flow goes down — not up."],
        "revision_plan": [{"task": "Three practice problems on the core relationship", "minutes": 10, "why": "the direction error needs repetition, not re-reading"}],
        "next_topic": {"name": "Applying the idea to circuits", "reason": "it reuses the relationship you just learned"},
        "study_tip": "Say the direction out loud before you compute anything.",
    }


def _path(user: str) -> dict:
    return {
        "topic": "Sample Path",
        "total_hours": 24,
        "modules": [
            {"order": i + 1, "name": n, "outcome": f"You can use {n.lower()}", "hours": 4,
             "prerequisites": [], "subtopics": ["part one", "part two"],
             "checkpoint_project": "Build a small example", "commonly_stalls_here": i == 1}
            for i, n in enumerate(["Fundamentals", "Core Techniques", "Practice", "Advanced Work"])
        ],
    }


def _follow_up() -> dict:
    return {
        "answer": "Good question. Short version: it's the same rule applied twice. We'll do it properly in two minutes.",
        "relates_to_concept": "concept_2",
        "reveals_gap_in": None,
        "is_ahead_of_lesson": True,
        "citations": ["c0"],
    }
