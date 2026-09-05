"""Upload -> blocks -> chunks -> index, with a document summary for the UI."""
from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from pathlib import Path

from backend.config import settings
from backend.ingestion.chunker import chunk_blocks
from backend.ingestion.loaders import load
from backend.rag.vector_store import VectorStore

log = logging.getLogger(__name__)

# Script ranges used to guess the document language without a heavy dependency.
_SCRIPTS = {
    "hi": ("\u0900", "\u097f"), "bn": ("\u0980", "\u09ff"), "pa": ("\u0a00", "\u0a7f"),
    "gu": ("\u0a80", "\u0aff"), "or": ("\u0b00", "\u0b7f"), "ta": ("\u0b80", "\u0bff"),
    "te": ("\u0c00", "\u0c7f"), "kn": ("\u0c80", "\u0cff"), "ml": ("\u0d00", "\u0d7f"),
    "ar": ("\u0600", "\u06ff"), "zh": ("\u4e00", "\u9fff"), "ja": ("\u3040", "\u30ff"),
}


def doc_id_for(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.name.encode("utf-8"))
    with open(path, "rb") as fh:
        while block := fh.read(1 << 20):
            h.update(block)
    return h.hexdigest()[:16]


def ingest(path: str | Path, *, original_name: str | None = None) -> dict:
    path = Path(path)
    doc_id = doc_id_for(path)
    meta_path = settings.index_dir / doc_id / "meta.json"

    if meta_path.exists():
        log.info("Document %s already indexed; reusing.", doc_id)
        return json.loads(meta_path.read_text(encoding="utf-8"))

    blocks = load(path)
    if not blocks:
        raise ValueError(
            "No text could be extracted. If this is a scanned PDF, run "
            "`python scripts/ocr.py <file>` first."
        )
    chunks = chunk_blocks(blocks)
    VectorStore(doc_id).build(chunks)

    sample = " ".join(c.text for c in chunks[:25])
    meta = {
        "doc_id": doc_id,
        "filename": original_name or path.name,
        "size_bytes": path.stat().st_size,
        "blocks": len(blocks),
        "chunks": len(chunks),
        "pages": max((c.page or 0) for c in chunks) or None,
        "language": detect_language(sample),
        "sections": VectorStore.load(doc_id).sections()[:60],
        "outline": _outline(chunks),
        "preview": chunks[0].text[:400] if chunks else "",
    }
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Indexed %s: %d chunks across %d blocks.", meta["filename"], len(chunks), len(blocks))
    return meta


def get_meta(doc_id: str) -> dict:
    meta_path = settings.index_dir / doc_id / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"Unknown document '{doc_id}'")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def list_documents() -> list[dict]:
    out = []
    for meta_path in sorted(settings.index_dir.glob("*/meta.json")):
        try:
            out.append(json.loads(meta_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


def detect_language(text: str) -> str:
    """Script-based guess. Good enough to warn 'this book is in Hindi' and to
    pick a default teaching language; the student can always override."""
    if not text:
        return "unknown"
    counts = Counter()
    for ch in text[:4000]:
        for code, (lo, hi) in _SCRIPTS.items():
            if lo <= ch <= hi:
                counts[code] += 1
                break
        else:
            if ch.isascii() and ch.isalpha():
                counts["en"] += 1
    return counts.most_common(1)[0][0] if counts else "unknown"


def _outline(chunks) -> list[dict]:
    """Top-level chapter list for the scope picker."""
    seen: dict[str, dict] = {}
    for c in chunks:
        if not c.heading_path:
            continue
        top = c.heading_path[0]
        entry = seen.setdefault(top, {"title": top, "chunks": 0, "page_start": c.page, "subsections": []})
        entry["chunks"] += 1
        if len(c.heading_path) > 1 and c.heading_path[1] not in entry["subsections"]:
            entry["subsections"].append(c.heading_path[1])
    return list(seen.values())[:40]
