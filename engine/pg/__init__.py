"""PostgreSQL retrieval backend (Full-Text Search + pgvector).

A drop-in alternative to the Elasticsearch backend that needs no Elasticsearch
cluster — just a Postgres database with the ``vector`` extension — so the whole
platform can run on free tiers (e.g. Render free web + free Postgres).

- :mod:`engine.pg.store`  — schema management + bulk upsert of documents.
- :mod:`engine.pg.search` — hybrid retrieval: Postgres FTS (BM25-like) + kNN
  cosine over pgvector, fused with the same Reciprocal Rank Fusion used by the
  Elasticsearch backend.

Selected at runtime via ``ENGINE_BACKEND=postgres`` (see :mod:`engine.backend`).
"""
