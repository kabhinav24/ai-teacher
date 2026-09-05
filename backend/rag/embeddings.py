"""Embedding backends.

Default is a multilingual sentence-transformer so a Hindi query can retrieve
from an English textbook and vice versa — required by the multilingual brief,
and not something a monolingual model gives you.
"""
from __future__ import annotations

import hashlib
import logging
import re

import numpy as np

from backend.config import settings

log = logging.getLogger(__name__)


class Embedder:
    dim: int = 384

    def encode(self, texts: list[str], *, is_query: bool = False) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError


class SentenceTransformerEmbedder(Embedder):
    """intfloat/multilingual-e5-small by default: 384-dim, ~100 languages,
    runs on CPU, and puts Hindi and English text in a shared space."""

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(settings.embeddings.model)
        self.dim = self._model.get_sentence_embedding_dimension()
        self._needs_prefix = "e5" in settings.embeddings.model.lower()

    def encode(self, texts: list[str], *, is_query: bool = False) -> np.ndarray:
        if self._needs_prefix:
            prefix = "query: " if is_query else "passage: "
            texts = [prefix + t for t in texts]
        vecs = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)


class OpenAIEmbedder(Embedder):
    def __init__(self) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=settings.embeddings.api_key or None)
        self.dim = settings.embeddings.dim

    def encode(self, texts: list[str], *, is_query: bool = False) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), 128):
            resp = self._client.embeddings.create(model=settings.embeddings.model, input=texts[i : i + 128])
            out.extend(d.embedding for d in resp.data)
        arr = np.asarray(out, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / np.clip(norms, 1e-9, None)


class HashingEmbedder(Embedder):
    """Dependency-free fallback so the pipeline runs anywhere.

    Hashed character n-grams. Genuinely worse than a trained model — it has no
    cross-lingual signal — but it keeps the app functional offline, and the
    hybrid retriever's lexical half carries most of the load in that mode.
    """

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings.embeddings.dim

    def encode(self, texts: list[str], *, is_query: bool = False) -> np.ndarray:
        vecs = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feat in self._features(text):
                h = int.from_bytes(hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest(), "big")
                vecs[row, h % self.dim] += 1.0 if h % 2 else -1.0
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.clip(norms, 1e-9, None)

    @staticmethod
    def _features(text: str) -> list[str]:
        text = text.lower()
        words = re.findall(r"\w+", text)
        feats = list(words)
        feats += [f"{a}_{b}" for a, b in zip(words, words[1:])]
        for w in words:
            if len(w) > 4:
                feats += [w[i : i + 4] for i in range(len(w) - 3)]
        return feats


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        provider = settings.embeddings.provider.lower()
        try:
            if provider == "sentence_transformers":
                _embedder = SentenceTransformerEmbedder()
            elif provider == "openai":
                _embedder = OpenAIEmbedder()
            else:
                _embedder = HashingEmbedder()
        except Exception as exc:  # noqa: BLE001
            log.warning("Embedding provider '%s' unavailable (%s). Falling back to hashing.", provider, exc)
            _embedder = HashingEmbedder()
    return _embedder
