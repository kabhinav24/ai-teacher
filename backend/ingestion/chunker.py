"""Chunking that respects document structure.

Chunks never span a heading boundary, and every chunk carries its heading trail
into the embedded text. That heading trail is what lets "teach me chapter 4"
retrieve chapter 4 rather than whatever paragraph is lexically closest.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from backend.config import settings
from backend.ingestion.loaders import Block

_SENT_SPLIT = re.compile(r"(?<=[.!?।])\s+|\n(?=[A-Z\u0900-\u097F])")


@dataclass
class Chunk:
    id: str
    text: str
    heading_path: list[str] = field(default_factory=list)
    page: int | None = None
    kind: str = "text"
    token_estimate: int = 0
    ordinal: int = 0

    @property
    def label(self) -> str:
        trail = " > ".join(self.heading_path[-2:]) if self.heading_path else ""
        page = f"p.{self.page}" if self.page else ""
        return " · ".join(x for x in (trail, page) if x) or f"chunk {self.ordinal}"

    def for_embedding(self) -> str:
        prefix = " > ".join(self.heading_path)
        return f"{prefix}\n{self.text}" if prefix else self.text

    def as_dict(self) -> dict:
        d = asdict(self)
        d["label"] = self.label
        return d


def estimate_tokens(text: str) -> int:
    """Cheap, script-aware token estimate. Devanagari runs ~1 token per 2 chars."""
    if not text:
        return 0
    deva = sum(1 for c in text if "\u0900" <= c <= "\u097f")
    if deva > len(text) * 0.3:
        return max(1, len(text) // 2)
    return max(1, len(text) // 4)


def chunk_blocks(
    blocks: list[Block],
    *,
    target_tokens: int | None = None,
    overlap_tokens: int | None = None,
) -> list[Chunk]:
    target = target_tokens or settings.rag.chunk_tokens
    overlap = overlap_tokens or settings.rag.chunk_overlap

    chunks: list[Chunk] = []
    buffer: list[str] = []
    buf_tokens = 0
    cur_path: list[str] = []
    cur_page: int | None = None

    def flush(kind: str = "text") -> None:
        nonlocal buffer, buf_tokens
        text = "\n".join(buffer).strip()
        if text:
            idx = len(chunks)
            chunks.append(
                Chunk(
                    id=f"c{idx}",
                    text=text,
                    heading_path=list(cur_path),
                    page=cur_page,
                    kind=kind,
                    token_estimate=estimate_tokens(text),
                    ordinal=idx,
                )
            )
        buffer, buf_tokens = [], 0

    for block in blocks:
        # A heading closes the previous chunk: never merge across sections.
        if block.kind == "heading":
            flush()
            cur_path = block.heading_path or [block.text.strip()]
            cur_page = block.page
            continue

        if block.heading_path != cur_path:
            flush()
            cur_path = block.heading_path
        if block.page is not None:
            cur_page = block.page

        # Tables and code are atomic — splitting them destroys their meaning.
        if block.kind in {"table", "code"}:
            flush()
            buffer = [block.text]
            buf_tokens = estimate_tokens(block.text)
            flush(block.kind)
            continue

        for sentence in _split_sentences(block.text):
            s_tokens = estimate_tokens(sentence)
            if buf_tokens + s_tokens > target and buffer:
                tail = _tail(buffer, overlap)
                flush()
                buffer = list(tail)
                buf_tokens = sum(estimate_tokens(t) for t in buffer)
            buffer.append(sentence)
            buf_tokens += s_tokens

    flush()
    # Drop stray fragments, but never an atomic table or code block — a short
    # table is still the whole table.
    return [c for c in chunks if len(c.text) > 25 or c.kind in {"table", "code"}]


def _split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p and p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _tail(buffer: list[str], overlap_tokens: int) -> list[str]:
    """Carry the last sentences forward so a concept split across a boundary
    still appears whole in one chunk."""
    out: list[str] = []
    total = 0
    for sentence in reversed(buffer):
        t = estimate_tokens(sentence)
        if total + t > overlap_tokens and out:
            break
        out.insert(0, sentence)
        total += t
    return out
