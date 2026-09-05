"""Retrieval + grounding verification.

Retrieval alone does not stop hallucination — the model can still ignore the
context it was given. `verify_grounding` checks the produced narration back
against the retrieved chunks and reports unsupported sentences, which the API
surfaces so the UI can flag them. That check is the difference between "we used
RAG" and "we can show it worked".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from backend.config import settings
from backend.rag.embeddings import get_embedder
from backend.rag.vector_store import Hit, VectorStore, tokenize

_SENT = re.compile(r"(?<=[.!?।])\s+")


@dataclass
class GroundingReport:
    score: float                    # 0-1, share of checkable sentences supported
    supported: list[str]
    unsupported: list[str]
    citations_used: list[str]
    passed: bool


def retrieve(doc_id: str, query: str, *, top_k: int | None = None, section: str | None = None) -> list[Hit]:
    return VectorStore.load(doc_id).search(query, top_k=top_k, section=section)


def retrieve_multi(doc_id: str, queries: list[str], *, per_query: int = 4, limit: int = 12) -> list[Hit]:
    """Retrieve for several sub-questions at once and de-duplicate.

    The planner needs breadth across a chapter, not depth on one phrase, so it
    issues one query per candidate concept and merges the results.
    """
    store = VectorStore.load(doc_id)
    best: dict[str, Hit] = {}
    for q in queries:
        for hit in store.search(q, top_k=per_query):
            prev = best.get(hit.chunk.id)
            if prev is None or hit.score > prev.score:
                best[hit.chunk.id] = hit
    ordered = sorted(best.values(), key=lambda h: h.chunk.ordinal)
    return ordered[:limit]


def pack_context(hits: list[Hit], *, max_tokens: int = 3000) -> str:
    """Render hits as a citable context block."""
    parts: list[str] = []
    used = 0
    for hit in hits:
        c = hit.chunk
        header = f"[{c.id}] {c.label}"
        body = c.text.strip()
        cost = c.token_estimate or len(body) // 4
        if used + cost > max_tokens:
            break
        parts.append(f"{header}\n{body}")
        used += cost
    return "\n\n---\n\n".join(parts) if parts else "(no source material — teach from general knowledge)"


def verify_grounding(text: str, hits: list[Hit], *, threshold: float | None = None) -> GroundingReport:
    """Sentence-level entailment proxy.

    For each factual sentence, take the max of lexical overlap and embedding
    similarity against every retrieved chunk. Sentences that are questions,
    transitions or second-person address are skipped — a teacher saying "now
    look at the screen" is not a claim that needs a source.
    """
    threshold = threshold if threshold is not None else settings.rag.min_grounding_score
    if not hits:
        return GroundingReport(1.0, [], [], [], True)

    sentences = [s.strip() for s in _SENT.split(text or "") if len(s.strip()) > 25]
    checkable = [s for s in sentences if _is_factual(s)]
    if not checkable:
        return GroundingReport(1.0, [], [], _cited(text), True)

    chunk_texts = [h.chunk.text for h in hits]
    chunk_tokens = [set(tokenize(t)) for t in chunk_texts]

    embedder = get_embedder()
    try:
        sent_vecs = embedder.encode(checkable, is_query=True)
        chunk_vecs = embedder.encode(chunk_texts)
        sims = sent_vecs @ chunk_vecs.T
    except Exception:  # noqa: BLE001 - embedding is best-effort here
        sims = np.zeros((len(checkable), len(chunk_texts)), dtype=np.float32)

    supported: list[str] = []
    unsupported: list[str] = []
    for i, sentence in enumerate(checkable):
        s_tokens = set(tokenize(sentence))
        content = {t for t in s_tokens if len(t) > 3}
        lexical = max(
            (len(content & ct) / len(content) if content else 0.0) for ct in chunk_tokens
        )
        dense = float(sims[i].max()) if sims.size else 0.0
        if max(lexical, dense) >= threshold:
            supported.append(sentence)
        else:
            unsupported.append(sentence)

    score = len(supported) / len(checkable)
    return GroundingReport(
        score=round(score, 3),
        supported=supported,
        unsupported=unsupported,
        citations_used=_cited(text),
        passed=score >= 0.6,
    )


_HEDGE = re.compile(r"^(now|so|next|let'?s|okay|right|alright|good|remember|notice|think|look|imagine|question)\b", re.I)


def _is_factual(sentence: str) -> bool:
    s = sentence.strip()
    if s.endswith("?"):
        return False
    if _HEDGE.match(s):
        return False
    if re.search(r"\byou\b", s, re.I) and not re.search(r"\d", s):
        return False
    return True


def _cited(text: str) -> list[str]:
    return sorted(set(re.findall(r"\[(c\d+)\]", text or "")))
