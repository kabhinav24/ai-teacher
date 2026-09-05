"""Hybrid retrieval store: dense vectors + BM25, fused by reciprocal rank.

Why hybrid rather than pure dense: textbook questions are full of exact tokens
that embeddings blur — "Ohm's Law", "Article 21", "Figure 4.3", "O(n log n)".
BM25 nails those; dense handles paraphrase and cross-language. Fusing the two
ranks is more robust than tuning a single score threshold.
"""
from __future__ import annotations

import json
import math
import pickle
import re
from collections import Counter
from dataclasses import dataclass

import numpy as np

from backend.config import settings
from backend.ingestion.chunker import Chunk
from backend.rag.embeddings import get_embedder

_TOKEN = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass
class Hit:
    chunk: Chunk
    score: float
    dense_rank: int | None = None
    lexical_rank: int | None = None


class BM25:
    """Okapi BM25 over the chunk corpus."""

    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.corpus = corpus
        self.n = len(corpus)
        self.doc_len = [len(d) for d in corpus]
        self.avg_len = (sum(self.doc_len) / self.n) if self.n else 0.0
        self.tf: list[Counter] = [Counter(d) for d in corpus]
        df: Counter = Counter()
        for d in corpus:
            df.update(set(d))
        self.idf = {
            term: math.log(1 + (self.n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()
        }

    def scores(self, query: list[str]) -> np.ndarray:
        out = np.zeros(self.n, dtype=np.float32)
        for term in query:
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i, tf in enumerate(self.tf):
                f = tf.get(term, 0)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * self.doc_len[i] / (self.avg_len or 1))
                out[i] += idf * (f * (self.k1 + 1)) / denom
        return out


class VectorStore:
    def __init__(self, doc_id: str) -> None:
        self.doc_id = doc_id
        self.dir = settings.index_dir / doc_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.chunks: list[Chunk] = []
        self.vectors: np.ndarray | None = None
        self._bm25: BM25 | None = None

    # -- build ------------------------------------------------------------
    def build(self, chunks: list[Chunk]) -> VectorStore:
        self.chunks = chunks
        texts = [c.for_embedding() for c in chunks]
        self.vectors = get_embedder().encode(texts) if texts else np.zeros((0, 1), dtype=np.float32)
        self._bm25 = BM25([tokenize(t) for t in texts])
        self.save()
        return self

    def save(self) -> None:
        np.save(self.dir / "vectors.npy", self.vectors if self.vectors is not None else np.zeros((0, 1)))
        (self.dir / "chunks.json").write_text(
            json.dumps([c.as_dict() for c in self.chunks], ensure_ascii=False), encoding="utf-8"
        )
        with open(self.dir / "bm25.pkl", "wb") as fh:
            pickle.dump(self._bm25, fh)

    @classmethod
    def load(cls, doc_id: str) -> VectorStore:
        store = cls(doc_id)
        chunk_file = store.dir / "chunks.json"
        if not chunk_file.exists():
            raise FileNotFoundError(f"No index for document '{doc_id}'. Ingest it first.")
        raw = json.loads(chunk_file.read_text(encoding="utf-8"))
        store.chunks = [
            Chunk(
                id=c["id"],
                text=c["text"],
                heading_path=c.get("heading_path", []),
                page=c.get("page"),
                kind=c.get("kind", "text"),
                token_estimate=c.get("token_estimate", 0),
                ordinal=c.get("ordinal", 0),
            )
            for c in raw
        ]
        store.vectors = np.load(store.dir / "vectors.npy")
        bm25_file = store.dir / "bm25.pkl"
        if bm25_file.exists():
            with open(bm25_file, "rb") as fh:
                store._bm25 = pickle.load(fh)
        else:
            store._bm25 = BM25([tokenize(c.for_embedding()) for c in store.chunks])
        return store

    # -- query ------------------------------------------------------------
    def search(self, query: str, *, top_k: int | None = None, section: str | None = None) -> list[Hit]:
        """Reciprocal-rank fusion of dense and lexical rankings.

        `section` restricts to chunks whose heading trail matches, which is how
        "teach me Chapter 4" becomes a real scope filter instead of a hope.
        """
        if not self.chunks:
            return []
        k = top_k or settings.rag.top_k

        candidate_idx = list(range(len(self.chunks)))
        if section:
            needle = section.lower().strip()
            filtered = [
                i for i, c in enumerate(self.chunks)
                if needle in " > ".join(c.heading_path).lower() or needle in c.text[:120].lower()
            ]
            if filtered:
                candidate_idx = filtered

        qvec = get_embedder().encode([query], is_query=True)[0]
        dense_all = (
            self.vectors @ qvec
            if self.vectors is not None and self.vectors.ndim == 2 and self.vectors.shape[0] == len(self.chunks)
            else np.zeros(len(self.chunks), dtype=np.float32)
        )
        lex_all = self._bm25.scores(tokenize(query)) if self._bm25 else np.zeros(len(self.chunks), dtype=np.float32)

        dense_rank = _ranks([dense_all[i] for i in candidate_idx])
        lex_rank = _ranks([lex_all[i] for i in candidate_idx])

        w = settings.rag.dense_weight
        fused: list[tuple[int, float, int, int]] = []
        for pos, idx in enumerate(candidate_idx):
            rrf = w / (60 + dense_rank[pos]) + (1 - w) / (60 + lex_rank[pos])
            fused.append((idx, rrf, dense_rank[pos], lex_rank[pos]))
        fused.sort(key=lambda t: t[1], reverse=True)

        hits = [
            Hit(chunk=self.chunks[i], score=round(float(s) * 1000, 4), dense_rank=dr, lexical_rank=lr)
            for i, s, dr, lr in fused[:k]
        ]
        return hits

    def sections(self) -> list[dict]:
        """Chapter/section outline, used to let a student pick scope in the UI."""
        seen: dict[str, dict] = {}
        for c in self.chunks:
            if not c.heading_path:
                continue
            key = " > ".join(c.heading_path)
            entry = seen.setdefault(key, {"path": c.heading_path, "label": key, "chunks": 0, "pages": set()})
            entry["chunks"] += 1
            if c.page:
                entry["pages"].add(c.page)
        out = []
        for e in seen.values():
            pages = sorted(e.pop("pages"))
            e["page_start"], e["page_end"] = (pages[0], pages[-1]) if pages else (None, None)
            out.append(e)
        return out


def _ranks(values: list[float]) -> list[int]:
    order = sorted(range(len(values)), key=lambda i: values[i], reverse=True)
    ranks = [0] * len(values)
    for rank, idx in enumerate(order, start=1):
        ranks[idx] = rank
    return ranks
