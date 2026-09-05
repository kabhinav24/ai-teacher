"""Persistent learner profile, sessions and progress.

The learner profile is what makes the *second* lesson better than the first:
mastery estimates and misconceptions carry across sessions, so the planner can
skip what a student already owns and revisit what they never fixed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


def _uid() -> str:
    return uuid.uuid4().hex[:16]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Student(Base):
    __tablename__ = "students"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(120), default="Student")
    level: Mapped[str] = mapped_column(String(20), default="beginner")
    preferred_language: Mapped[str] = mapped_column(String(20), default="en")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    sessions: Mapped[list["LessonSession"]] = relationship(back_populates="student", cascade="all, delete-orphan")
    concepts: Mapped[list["ConceptRecord"]] = relationship(back_populates="student", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    filename: Mapped[str] = mapped_column(String(300))
    language: Mapped[str] = mapped_column(String(20), default="unknown")
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LessonSession(Base):
    __tablename__ = "lesson_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"))
    doc_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    topic: Mapped[str] = mapped_column(String(300), default="")
    language: Mapped[str] = mapped_column(String(20), default="en")
    level: Mapped[str] = mapped_column(String(20), default="beginner")
    minutes: Mapped[float] = mapped_column(Float, default=20.0)
    status: Mapped[str] = mapped_column(String(20), default="active")
    state: Mapped[dict] = mapped_column(JSON, default=dict)     # serialised TeachingSession
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    video_url: Mapped[str | None] = mapped_column(String(400), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    student: Mapped[Student] = relationship(back_populates="sessions")


class ConceptRecord(Base):
    """Long-term per-concept memory, updated at the end of every session."""

    __tablename__ = "concept_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"))
    concept_key: Mapped[str] = mapped_column(String(120))
    concept_name: Mapped[str] = mapped_column(String(300))
    topic: Mapped[str] = mapped_column(String(300), default="")
    mastery: Mapped[float] = mapped_column(Float, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    correct: Mapped[int] = mapped_column(Integer, default=0)
    misconceptions: Mapped[list] = mapped_column(JSON, default=list)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    student: Mapped[Student] = relationship(back_populates="concepts")


class LearningPath(Base):
    __tablename__ = "learning_paths"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"))
    topic: Mapped[str] = mapped_column(String(300))
    path: Mapped[dict] = mapped_column(JSON, default=dict)
    schedule: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Note(Base):
    """Auto-generated revision notes and flashcards from a finished lesson."""

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("lesson_sessions.id"))
    kind: Mapped[str] = mapped_column(String(20), default="note")   # note | flashcard
    front: Mapped[str] = mapped_column(Text, default="")
    back: Mapped[str] = mapped_column(Text, default="")
    concept_key: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
