"""Local sentence-embedding generation.

The embedder is intentionally forgiving:

* If :mod:`sentence_transformers` is installed, it loads the configured model
  once (lazily) and encodes on CPU/GPU per configuration.
* Otherwise — or when ``ENGINE_EMBEDDING_FALLBACK`` is set — it produces a
  deterministic hashing-based pseudo-embedding of the correct dimensionality.
  The fallback is *not* semantically meaningful, but it keeps the ingestion and
  search pipelines fully exercisable in CI and bare dev environments, and it is
  stable (the same text always maps to the same vector).

All vectors are L2-normalized so cosine similarity reduces to a dot product,
matching the Elasticsearch ``dense_vector`` ``cosine`` similarity.
"""

from __future__ import annotations

import hashlib
import math
import threading
from typing import List, Optional, Sequence

from engine.config import EngineConfig, get_config


class Embedder:
    """Encodes text into dense vectors, with a graceful no-ML fallback."""

    def __init__(self, config: Optional[EngineConfig] = None) -> None:
        self.config = config or get_config()
        self.dims = self.config.embedding_dims
        self._model = None
        self._model_lock = threading.Lock()
        self._tried_load = False

    # ------------------------------------------------------------------ #
    # Model management
    # ------------------------------------------------------------------ #
    @property
    def using_model(self) -> bool:
        """True when a real sentence-transformer model is loaded."""
        return self._model is not None

    def _load_model(self):
        if self.config.embedding_force_fallback:
            return None
        if self._model is not None or self._tried_load:
            return self._model
        with self._model_lock:
            if self._model is not None or self._tried_load:
                return self._model
            self._tried_load = True
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore

                model = SentenceTransformer(
                    self.config.embedding_model,
                    device=self.config.embedding_device,
                )
                # Trust the model's real dimensionality if it differs.
                dim = model.get_sentence_embedding_dimension()
                if dim:
                    self.dims = int(dim)
                self._model = model
            except Exception:
                # Any failure (missing package, no network for weights, OOM) is
                # non-fatal: we degrade to the deterministic fallback.
                self._model = None
            return self._model

    # ------------------------------------------------------------------ #
    # Encoding
    # ------------------------------------------------------------------ #
    def encode(self, text: str) -> List[float]:
        """Encode a single string into a normalized vector."""
        return self.encode_batch([text])[0]

    def encode_batch(self, texts: Sequence[str]) -> List[List[float]]:
        """Encode a batch of strings into normalized vectors."""
        texts = [t or "" for t in texts]
        model = self._load_model()
        if model is not None:
            vectors = model.encode(
                list(texts),
                batch_size=self.config.embedding_batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            return [v.tolist() for v in vectors]
        return [self._fallback_vector(t) for t in texts]

    # ------------------------------------------------------------------ #
    # Deterministic fallback
    # ------------------------------------------------------------------ #
    def _fallback_vector(self, text: str) -> List[float]:
        """Hash text into a stable, L2-normalized pseudo-embedding.

        We hash overlapping token shingles into buckets, which gives vectors
        that at least move in the same direction for texts sharing vocabulary —
        enough to sanity-check the kNN plumbing end to end.
        """
        vec = [0.0] * self.dims
        tokens = _tokenize(text)
        if not tokens:
            vec[0] = 1.0
            return vec
        for token in tokens:
            h = hashlib.md5(token.encode("utf-8")).digest()
            bucket = int.from_bytes(h[:4], "big") % self.dims
            sign = 1.0 if h[4] & 1 else -1.0
            vec[bucket] += sign
        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0.0:
            vec[0] = 1.0
            return vec
        return [x / norm for x in vec]


def _tokenize(text: str) -> List[str]:
    return [
        t
        for t in "".join(
            c.lower() if c.isalnum() else " " for c in text
        ).split()
        if t
    ]


_default_embedder: Optional[Embedder] = None
_default_lock = threading.Lock()


def get_embedder() -> Embedder:
    """Return a process-wide shared embedder (loads the model at most once)."""
    global _default_embedder
    if _default_embedder is None:
        with _default_lock:
            if _default_embedder is None:
                _default_embedder = Embedder()
    return _default_embedder


# --------------------------------------------------------------------------- #
# Convenience API
# --------------------------------------------------------------------------- #
# Encode text into dense vectors natively on the host — no external embedding
# API, no per-token billing. With the default model
# (``sentence-transformers/all-MiniLM-L6-v2``) each vector is 384-dimensional
# and L2-normalized, matching the ``dense_vector`` field in the index.
def embed_text(text: str) -> List[float]:
    """Encode a single string into a 384-dim (by default) dense vector."""
    return get_embedder().encode(text)


def embed_texts(texts: Sequence[str]) -> List[List[float]]:
    """Encode a batch of strings into dense vectors (batched, on-host)."""
    return get_embedder().encode_batch(texts)


def embedding_dimension() -> int:
    """Dimensionality of the vectors produced by the active embedder."""
    return get_embedder().dims
