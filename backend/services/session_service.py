"""Lesson lifecycle and long-term learner memory."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from backend.agents.controller import TeachingSession
from backend.agents.pedagogy import readiness_score
from backend.agents.planner import build_profile, plan_lesson
from backend.models import ConceptRecord, LessonSession, Note, Student

log = logging.getLogger(__name__)

#: In-process cache of live sessions. State is also persisted to the DB on every
#: mutation, so a restart or a second worker loses nothing.
_LIVE: dict[str, TeachingSession] = {}


def get_or_create_student(db: DBSession, student_id: str | None, **defaults) -> Student:
    if student_id:
        student = db.get(Student, student_id)
        if student:
            return student
    student = Student(**{k: v for k, v in defaults.items() if v is not None})
    if student_id:
        student.id = student_id
    db.add(student)
    db.commit()
    db.refresh(student)
    return student


def start_lesson(
    db: DBSession,
    *,
    student_id: str | None,
    request_text: str,
    doc_id: str | None = None,
    section: str | None = None,
    overrides: dict | None = None,
) -> tuple[LessonSession, TeachingSession]:
    """Understand the request, load prior knowledge, plan the lesson."""
    student = get_or_create_student(db, student_id)
    profile = build_profile(request_text, defaults={
        "level": student.level, "language": student.preferred_language,
    })
    profile.update({k: v for k, v in (overrides or {}).items() if v is not None})

    prior = prior_knowledge(db, student.id)
    if prior:
        profile["known_concepts"] = [p["concept_name"] for p in prior if p["mastery"] >= 0.75][:20]
        profile["weak_concepts"] = [p["concept_name"] for p in prior if p["mastery"] < 0.5][:20]

    plan = plan_lesson(profile, doc_id=doc_id, section=section)
    teaching = TeachingSession(plan, profile, doc_id=doc_id)

    # Carry forward what this student already demonstrated.
    seeded = 0
    by_name = {p["concept_name"].lower(): p for p in prior}
    for key, state in teaching.states.items():
        match = by_name.get(state.name.lower())
        if match:
            state.p_known = max(state.p_known, float(match["mastery"]))
            state.misconceptions = list(match.get("misconceptions") or [])
            seeded += 1
    if seeded:
        teaching.decision_log.append({
            "turn": 0, "phase": "plan", "decision": "seed prior knowledge",
            "because": f"{seeded} concept(s) carried over from earlier lessons",
        })

    record = LessonSession(
        student_id=student.id, doc_id=doc_id, topic=plan.get("title", ""),
        language=profile.get("teaching_language", "en"), level=profile.get("level", "beginner"),
        minutes=float(profile.get("minutes", 20)), state=teaching.to_dict(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    _LIVE[record.id] = teaching
    return record, teaching


def load_session(db: DBSession, session_id: str) -> tuple[LessonSession, TeachingSession]:
    record = db.get(LessonSession, session_id)
    if not record:
        raise KeyError(f"Unknown session '{session_id}'")
    teaching = _LIVE.get(session_id)
    if teaching is None:
        teaching = TeachingSession.from_dict(record.state)
        _LIVE[session_id] = teaching
    return record, teaching


def persist(db: DBSession, record: LessonSession, teaching: TeachingSession) -> None:
    record.state = teaching.to_dict()
    record.language = teaching.profile.get("teaching_language", record.language)
    if teaching.report:
        record.status = "complete"
        record.score = teaching.report.get("score")
    db.add(record)
    db.commit()


def finalise(db: DBSession, record: LessonSession, teaching: TeachingSession) -> dict:
    """Write per-concept mastery back to long-term memory and generate notes."""
    score = readiness_score(teaching.states.values())
    for key, state in teaching.states.items():
        existing = db.scalar(
            select(ConceptRecord).where(
                ConceptRecord.student_id == record.student_id,
                ConceptRecord.concept_key == key,
            )
        )
        if existing:
            # Weighted toward the newer observation without discarding history.
            existing.mastery = round(0.35 * existing.mastery + 0.65 * state.p_known, 4)
            existing.attempts += state.attempts
            existing.correct += state.correct
            existing.misconceptions = sorted(set((existing.misconceptions or []) + state.misconceptions))
        else:
            db.add(ConceptRecord(
                student_id=record.student_id, concept_key=key, concept_name=state.name,
                topic=record.topic, mastery=round(state.p_known, 4),
                attempts=state.attempts, correct=state.correct,
                misconceptions=list(state.misconceptions),
            ))

    for note in _auto_notes(teaching):
        db.add(Note(session_id=record.id, **note))

    record.score = score
    record.status = "complete"
    db.commit()
    return {"score": score, "concepts_recorded": len(teaching.states)}


def prior_knowledge(db: DBSession, student_id: str) -> list[dict]:
    rows = db.scalars(
        select(ConceptRecord).where(ConceptRecord.student_id == student_id)
        .order_by(ConceptRecord.last_seen.desc()).limit(200)
    ).all()
    return [
        {"concept_key": r.concept_key, "concept_name": r.concept_name, "topic": r.topic,
         "mastery": r.mastery, "attempts": r.attempts, "correct": r.correct,
         "misconceptions": r.misconceptions or [], "last_seen": r.last_seen.isoformat() if r.last_seen else None}
        for r in rows
    ]


def progress_summary(db: DBSession, student_id: str) -> dict:
    records = prior_knowledge(db, student_id)
    sessions = db.scalars(
        select(LessonSession).where(LessonSession.student_id == student_id)
        .order_by(LessonSession.created_at.desc()).limit(50)
    ).all()
    strong = [r for r in records if r["mastery"] >= 0.75]
    weak = [r for r in records if r["mastery"] < 0.5]
    scored = [s.score for s in sessions if s.score is not None]
    return {
        "concepts_studied": len(records),
        "strong": sorted(strong, key=lambda r: -r["mastery"])[:12],
        "weak": sorted(weak, key=lambda r: r["mastery"])[:12],
        "sessions": [
            {"id": s.id, "topic": s.topic, "score": s.score, "language": s.language,
             "status": s.status, "created_at": s.created_at.isoformat() if s.created_at else None}
            for s in sessions[:20]
        ],
        "average_score": round(sum(scored) / len(scored), 1) if scored else None,
        "recurring_misconceptions": _recurring(records),
    }


def _recurring(records: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for r in records:
        for m in r["misconceptions"]:
            counts[m] = counts.get(m, 0) + 1
    return [{"tag": k, "seen_in_concepts": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1]) if v > 1][:6]


def _auto_notes(teaching: TeachingSession) -> list[dict]:
    """Revision notes and flashcards, straight from what was actually taught."""
    notes: list[dict] = []
    for turn in teaching.turns:
        if turn.kind not in {"explanation", "remediation"}:
            continue
        payload = turn.payload
        title = payload.get("board_title") or ""
        points = payload.get("board_points") or []
        if title and points:
            notes.append({"kind": "note", "concept_key": turn.concept_key or "",
                          "front": title, "back": "\n".join(str(p) for p in points)})
        for term in payload.get("key_terms") or []:
            if isinstance(term, dict) and term.get("term"):
                notes.append({"kind": "flashcard", "concept_key": turn.concept_key or "",
                              "front": str(term["term"]), "back": str(term.get("gloss", ""))})
    for turn in teaching.turns:
        if turn.kind == "question" and turn.payload.get("expected_answer"):
            notes.append({"kind": "flashcard", "concept_key": turn.concept_key or "",
                          "front": turn.payload.get("prompt", ""), "back": turn.payload["expected_answer"]})
    return notes[:60]
