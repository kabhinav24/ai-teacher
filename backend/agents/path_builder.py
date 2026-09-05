"""Learning paths for broad topics, and next-step selection within one."""
from __future__ import annotations


from backend.core import prompts
from backend.core.llm import complete_json


def build_path(topic: str, *, profile: dict) -> dict:
    user = f"""TOPIC: {topic}
LEARNER LEVEL: {profile.get('level', 'beginner')}
GOAL: {profile.get('goal', 'curiosity')}
TIME AVAILABLE: {profile.get('days', 7)} days, about {profile.get('hours_per_day', 1)} hour(s) per day
TEACHING LANGUAGE: {profile.get('language_label', 'English')}
KNOWN STRENGTHS: {profile.get('strong_concepts', [])}
KNOWN WEAKNESSES: {profile.get('weak_concepts', [])}
"""
    path = complete_json(prompts.PATH_BUILDER, user, temperature=0.4)
    modules = path.get("modules") or []
    for i, m in enumerate(modules):
        m["order"] = m.get("order", i + 1)
        m["status"] = "not_started"
        try:
            m["hours"] = float(m.get("hours", 2))
        except (TypeError, ValueError):
            m["hours"] = 2.0
    path["modules"] = sorted(modules, key=lambda m: m["order"])
    path["total_hours"] = round(sum(m["hours"] for m in path["modules"]), 1)
    return path


def schedule(path: dict, *, days: int, hours_per_day: float) -> list[dict]:
    """Spread a path across calendar days. Answers the '7 days' case in the brief:
    a time budget longer than a sitting becomes a plan, not one long lesson."""
    capacity = max(0.5, hours_per_day)
    plan, day, used = [], 1, 0.0
    current: list[dict] = []
    for m in path.get("modules", []):
        remaining = m["hours"]
        while remaining > 0 and day <= days:
            slot = min(remaining, capacity - used)
            if slot <= 0:
                plan.append({"day": day, "items": current, "hours": round(used, 1)})
                day, used, current = day + 1, 0.0, []
                continue
            current.append({"module": m["name"], "hours": round(slot, 1),
                            "outcome": m.get("outcome", ""),
                            "part": "continued" if slot < m["hours"] else "full"})
            used += slot
            remaining -= slot
    if current:
        plan.append({"day": day, "items": current, "hours": round(used, 1)})

    for entry in plan:
        entry["revision"] = [i["module"] for i in plan[max(0, entry["day"] - 3)]["items"]] if entry["day"] > 1 else []
    return plan


def next_module(path: dict, *, mastery: dict[str, float], threshold: float = 0.75) -> dict | None:
    """Pick the next module: the first whose prerequisites are all above threshold."""
    done = {m["name"] for m in path.get("modules", []) if mastery.get(m["name"], 0.0) >= threshold}
    for m in path.get("modules", []):
        if m["name"] in done:
            continue
        if all(p in done for p in m.get("prerequisites", [])):
            return m
    return next((m for m in path.get("modules", []) if m["name"] not in done), None)
