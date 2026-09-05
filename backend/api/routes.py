"""HTTP API.

One router, grouped by resource. Video rendering runs in a BackgroundTask and
reports progress through a status file, so the browser is never blocked on a
multi-minute ffmpeg job.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from backend.agents import path_builder
from backend.agents.visuals import shortlist_renderers
from backend.config import settings
from backend.database import get_db
from backend.ingestion import pipeline
from backend.ingestion.loaders import SUPPORTED
from backend.media import stt
from backend.models import Document, LearningPath, Note
from backend.rag.retriever import retrieve
from backend.services import session_service
from backend.services.video_service import VideoJob

log = logging.getLogger(__name__)
router = APIRouter()


# ------------------------------------------------------------------ schemas

class StartLesson(BaseModel):
    request: str = Field(..., description="Free-text instruction, e.g. 'I am a beginner. Teach me Chapter 4 in 20 minutes in Hindi.'")
    student_id: str | None = None
    doc_id: str | None = None
    section: str | None = None
    level: str | None = None
    teaching_language: str | None = None
    minutes: float | None = None


class AnswerIn(BaseModel):
    answer: str


class FollowUpIn(BaseModel):
    question: str


class LanguageIn(BaseModel):
    language: str
    label: str | None = None


class PathIn(BaseModel):
    topic: str
    student_id: str | None = None
    level: str = "beginner"
    days: int = 7
    hours_per_day: float = 1.0
    language_label: str = "English"


# ---------------------------------------------------------------- documents

@router.post("/documents", tags=["documents"])
async def upload_document(file: UploadFile = File(...), db: DBSession = Depends(get_db)):
    """Upload a book, PDF, notes, DOCX or PPTX and index it for retrieval."""
    suffix = Path(file.filename or "upload").suffix.lower()
    if suffix not in SUPPORTED:
        raise HTTPException(415, f"Unsupported file type '{suffix}'.")

    dest = settings.upload_dir / (file.filename or "upload")
    size = 0
    with open(dest, "wb") as fh:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > settings.max_upload_mb * 1024 * 1024:
                fh.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"File exceeds {settings.max_upload_mb} MB.")
            fh.write(chunk)

    try:
        meta = pipeline.ingest(dest, original_name=file.filename)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    if not db.get(Document, meta["doc_id"]):
        db.add(Document(
            id=meta["doc_id"], filename=meta["filename"], language=meta["language"],
            chunks=meta["chunks"], pages=meta.get("pages"), meta=meta,
        ))
        db.commit()
    return meta


@router.get("/documents", tags=["documents"])
def list_documents():
    return pipeline.list_documents()


@router.get("/documents/{doc_id}", tags=["documents"])
def get_document(doc_id: str):
    try:
        return pipeline.get_meta(doc_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/documents/{doc_id}/search", tags=["documents"])
def search_document(doc_id: str, q: str, k: int = 6, section: str | None = None):
    """Exposed so the grounding of any answer can be inspected directly."""
    try:
        hits = retrieve(doc_id, q, top_k=k, section=section)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return [
        {"id": h.chunk.id, "label": h.chunk.label, "page": h.chunk.page, "score": h.score,
         "dense_rank": h.dense_rank, "lexical_rank": h.lexical_rank, "text": h.chunk.text[:600]}
        for h in hits
    ]


# ----------------------------------------------------------------- lessons

@router.post("/lessons", tags=["lessons"])
def start_lesson(body: StartLesson, db: DBSession = Depends(get_db)):
    """Understand the request, retrieve, and produce the lesson plan."""
    try:
        record, teaching = session_service.start_lesson(
            db, student_id=body.student_id, request_text=body.request,
            doc_id=body.doc_id, section=body.section,
            overrides={"level": body.level, "teaching_language": body.teaching_language,
                       "minutes": body.minutes},
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc

    return {
        "session_id": record.id,
        "student_id": record.student_id,
        "profile": teaching.profile,
        "plan": teaching.plan,
        "time_plan": teaching.plan.get("time_plan"),
        "decision_log": teaching.decision_log,
    }


@router.post("/lessons/{session_id}/step", tags=["lessons"])
def step_lesson(session_id: str, db: DBSession = Depends(get_db)):
    """Advance to the teacher's next action."""
    record, teaching = _load(db, session_id)
    result = teaching.step()
    if result.get("type") == "report":
        session_service.finalise(db, record, teaching)
    session_service.persist(db, record, teaching)
    result["phase"] = teaching.phase.value
    result["mastery"] = teaching._mastery_snapshot()
    result["decision_log"] = teaching.decision_log[-4:]
    return result


@router.post("/lessons/{session_id}/answer", tags=["lessons"])
def answer(session_id: str, body: AnswerIn, db: DBSession = Depends(get_db)):
    """Submit a student response; the teacher grades it and adapts."""
    record, teaching = _load(db, session_id)
    result = teaching.submit_answer(body.answer)
    session_service.persist(db, record, teaching)
    result["decision_log"] = teaching.decision_log[-3:]
    return result


@router.post("/lessons/{session_id}/answer/voice", tags=["lessons"])
async def answer_by_voice(
    session_id: str,
    audio: UploadFile = File(...),
    db: DBSession = Depends(get_db),
):
    """Answer the open question by speaking instead of typing.

    Transcribed in the lesson's teaching language, then graded through exactly
    the same path as a typed answer — the evaluator marks the idea, not the
    wording, so transcription noise costs very little.
    """
    record, teaching = _load(db, session_id)
    if not teaching.pending_question:
        raise HTTPException(409, "No question is open.")

    suffix = Path(audio.filename or "answer.webm").suffix or ".webm"
    tmp = settings.upload_dir / f"voice_{session_id}{suffix}"
    tmp.write_bytes(await audio.read())

    try:
        transcript = stt.transcribe(tmp, lang=teaching.profile.get("teaching_language", "en"))
    except stt.STTUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"Could not transcribe that recording: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)
        tmp.with_suffix(".stt.wav").unlink(missing_ok=True)

    if not transcript.text.strip():
        raise HTTPException(422, "Nothing audible in that recording — try again.")

    result = teaching.submit_answer(transcript.text)
    session_service.persist(db, record, teaching)
    result["transcript"] = {"text": transcript.text, "provider": transcript.provider,
                            "confidence": transcript.confidence}
    result["decision_log"] = teaching.decision_log[-3:]
    return result


@router.post("/lessons/{session_id}/ask", tags=["lessons"])
def ask(session_id: str, body: FollowUpIn, db: DBSession = Depends(get_db)):
    """Interrupt with a question without losing the lesson's place."""
    record, teaching = _load(db, session_id)
    result = teaching.ask_follow_up(body.question)
    session_service.persist(db, record, teaching)
    return result


@router.post("/lessons/{session_id}/language", tags=["lessons"])
def switch_language(session_id: str, body: LanguageIn, db: DBSession = Depends(get_db)):
    """Change the teaching language mid-lesson. Plan and mastery are preserved."""
    record, teaching = _load(db, session_id)
    result = teaching.switch_language(body.language, body.label)
    session_service.persist(db, record, teaching)
    return result


@router.get("/lessons/{session_id}", tags=["lessons"])
def get_lesson(session_id: str, db: DBSession = Depends(get_db)):
    record, teaching = _load(db, session_id)
    return {
        "session_id": record.id, "status": record.status, "phase": teaching.phase.value,
        "plan": teaching.plan, "profile": teaching.profile,
        "mastery": teaching._mastery_snapshot(),
        "turns": [{"index": t.index, "kind": t.kind, "concept_key": t.concept_key, "payload": t.payload}
                  for t in teaching.turns],
        "decision_log": teaching.decision_log,
        "report": teaching.report, "video_url": record.video_url,
    }


@router.get("/lessons/{session_id}/notes", tags=["lessons"])
def get_notes(session_id: str, db: DBSession = Depends(get_db)):
    rows = db.query(Note).filter(Note.session_id == session_id).all()
    return {
        "notes": [{"front": n.front, "back": n.back, "concept": n.concept_key} for n in rows if n.kind == "note"],
        "flashcards": [{"front": n.front, "back": n.back, "concept": n.concept_key} for n in rows if n.kind == "flashcard"],
    }


# ------------------------------------------------------------------- video

@router.post("/lessons/{session_id}/video", tags=["video"])
def build_video(session_id: str, tasks: BackgroundTasks, db: DBSession = Depends(get_db)):
    """Render everything taught so far into a teaching video."""
    record, teaching = _load(db, session_id)
    segments = [
        {
            "kind": t.kind, "concept_key": t.concept_key,
            "narration": t.payload.get("narration", ""),
            "board_title": t.payload.get("board_title", ""),
            "board_points": t.payload.get("board_points", []),
            "visual": t.payload.get("visual"),
            "citations": t.payload.get("citations", []),
        }
        for t in teaching.turns if t.kind in {"intro", "explanation", "remediation"}
    ]
    if not segments:
        raise HTTPException(409, "Nothing has been taught yet. Step the lesson first.")

    job = VideoJob(session_id)
    job.set_status("queued", progress=0.0, message="Queued for rendering")
    record.video_url = f"/media/{session_id}/lesson.mp4"
    db.commit()

    tasks.add_task(job.build, segments,
                   lang=teaching.profile.get("teaching_language", "en"),
                   title=teaching.plan.get("title", "Lesson"))
    return {"session_id": session_id, "status": "queued", "poll": f"/api/lessons/{session_id}/video"}


@router.get("/lessons/{session_id}/video", tags=["video"])
def video_status(session_id: str):
    return VideoJob(session_id).get_status()


# ------------------------------------------------------------------ student

@router.get("/students/{student_id}/progress", tags=["students"])
def progress(student_id: str, db: DBSession = Depends(get_db)):
    return session_service.progress_summary(db, student_id)


@router.post("/paths", tags=["paths"])
def create_path(body: PathIn, db: DBSession = Depends(get_db)):
    """Generate a multi-day learning path — the '7 days' case from the brief."""
    student = session_service.get_or_create_student(db, body.student_id)
    prior = session_service.prior_knowledge(db, student.id)
    profile = {
        "level": body.level, "days": body.days, "hours_per_day": body.hours_per_day,
        "language_label": body.language_label,
        "strong_concepts": [p["concept_name"] for p in prior if p["mastery"] >= 0.75][:15],
        "weak_concepts": [p["concept_name"] for p in prior if p["mastery"] < 0.5][:15],
    }
    path = path_builder.build_path(body.topic, profile=profile)
    schedule = path_builder.schedule(path, days=body.days, hours_per_day=body.hours_per_day)
    record = LearningPath(student_id=student.id, topic=body.topic, path=path, schedule=schedule)
    db.add(record)
    db.commit()
    db.refresh(record)
    return {"path_id": record.id, "student_id": student.id, "path": path, "schedule": schedule}


@router.get("/paths/{path_id}", tags=["paths"])
def get_path(path_id: str, db: DBSession = Depends(get_db)):
    record = db.get(LearningPath, path_id)
    if not record:
        raise HTTPException(404, "Unknown path")
    prior = {p["concept_name"]: p["mastery"] for p in session_service.prior_knowledge(db, record.student_id)}
    return {"path_id": record.id, "topic": record.topic, "path": record.path,
            "schedule": record.schedule, "next_module": path_builder.next_module(record.path, mastery=prior)}


# ------------------------------------------------------------------- debug

@router.post("/debug/visual-choice", tags=["debug"])
def visual_choice(concept_name: str = Form(...), narration: str = Form(""), subject: str = Form("general")):
    """Show *why* a renderer was chosen. Backs the 'subject-aware visuals'
    requirement with an inspectable decision rather than a claim."""
    shortlist, trace = shortlist_renderers({"name": concept_name}, narration, subject=subject)
    return {"shortlist": shortlist, "trace": trace}


@router.get("/config", tags=["debug"])
def config():
    """Which providers are actually live. Doubles as the disclosure the brief
    asks for: every third-party service in use, visible at runtime."""
    return {
        "llm": {"provider": settings.llm.provider, "model": settings.llm.model,
                "configured": bool(settings.llm.api_key) or settings.llm.provider in {"ollama", "echo"}},
        "embeddings": {"provider": settings.embeddings.provider, "model": settings.embeddings.model},
        "tts": {"provider": settings.tts.provider, "configured": settings.tts.provider in {"edge", "gtts", "espeak", "silent"} or bool(settings.tts.api_key)},
        "stt": {"provider": settings.stt.provider, "model": settings.stt.model,
                "enabled": settings.stt.provider != "disabled"},
        "avatar": {"provider": settings.avatar.provider, "configured": settings.avatar.provider in {"static", "sadtalker"} or bool(settings.avatar.api_key)},
        "video": {"width": settings.video.width, "height": settings.video.height, "fps": settings.video.fps},
        "rag": {"top_k": settings.rag.top_k, "chunk_tokens": settings.rag.chunk_tokens,
                "dense_weight": settings.rag.dense_weight},
    }


def _load(db: DBSession, session_id: str):
    try:
        return session_service.load_session(db, session_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
