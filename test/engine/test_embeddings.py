"""Unit tests for the embedding fallback (no ML model required)."""

import math

from engine.config import EngineConfig
from engine.embeddings import Embedder


def _fallback_embedder(dims=384):
    # Force the deterministic hashing fallback regardless of installed packages.
    cfg = EngineConfig(embedding_force_fallback=True, embedding_dims=dims)
    return Embedder(cfg)


class TestEmbeddingFallback:
    def test_dimensionality(self):
        emb = _fallback_embedder(dims=128)
        assert len(emb.encode("cortex-m interrupts")) == 128

    def test_vectors_are_l2_normalized(self):
        emb = _fallback_embedder()
        v = emb.encode("finite element analysis of a bracket")
        assert math.isclose(sum(x * x for x in v), 1.0, rel_tol=1e-6)

    def test_deterministic(self):
        emb = _fallback_embedder()
        assert emb.encode("risc-v vector") == emb.encode("risc-v vector")

    def test_different_text_differs(self):
        emb = _fallback_embedder()
        assert emb.encode("kalman filter") != emb.encode("dma controller")

    def test_empty_text_is_unit_vector(self):
        emb = _fallback_embedder()
        v = emb.encode("")
        assert math.isclose(sum(x * x for x in v), 1.0, rel_tol=1e-6)

    def test_batch_matches_single(self):
        emb = _fallback_embedder()
        batch = emb.encode_batch(["a", "b"])
        assert batch[0] == emb.encode("a")
        assert len(batch) == 2

    def test_not_using_model_in_fallback(self):
        emb = _fallback_embedder()
        emb.encode("x")
        assert emb.using_model is False


class TestConvenienceApi:
    def test_embed_text_matches_dimension(self):
        from engine.embeddings import embed_text, embedding_dimension

        vec = embed_text("cortex-m interrupt latency")
        assert len(vec) == embedding_dimension()

    def test_embed_texts_batch(self):
        from engine.embeddings import embed_texts

        vecs = embed_texts(["dma", "adc"])
        assert len(vecs) == 2
        assert all(len(v) == len(vecs[0]) for v in vecs)
