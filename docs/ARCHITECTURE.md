# Architecture

Vers3Dynamics Engineering Intelligence layers a modern, AI-powered engineering search engine
on top of the existing Elasticsearch + Flask architecture. The two coexist: the legacy
book/paper search remains registered under `/legacy`, while the new platform owns the root.

## Layers

### 1. `engine/` — framework-agnostic search core

The `engine` package has **no Flask dependency** and imports every heavy dependency
(`torch`, `sentence-transformers`, `elasticsearch`, `httpx`, `pypdf`, `sqlalchemy`) **lazily**.
Importing `engine` therefore never fails, and the platform degrades gracefully when the ML
stack is absent.

| Module | Responsibility |
|---|---|
| `engine.config` | Immutable, env-driven configuration (`get_config()`, cached). |
| `engine.documents` | The unified `Document` schema shared by all sources and the index. |
| `engine.embeddings` | Local sentence embeddings with a deterministic hashing **fallback**. |
| `engine.index` | Elasticsearch hybrid index (mappings, create/reset, bulk index). |
| `engine.search` | Hybrid retrieval: BM25 + kNN fused with **Reciprocal Rank Fusion**. |
| `engine.summarize` | Citation-first extractive answers; optional local LLM; doc comparison. |
| `engine.collections` | PostgreSQL/SQLite collections & bookmarks (lazy ORM). |
| `engine.ingest` | Modular, plugin-based ingestion pipeline. |
| `engine.tasks` | Celery `shared_task`s for background indexing. |

### 2. `allthethings/` — Flask integration

| Blueprint | Mount | Purpose |
|---|---|---|
| `engine_web` | `/` | Modern server-rendered UI (home, search, document, compare, collections). |
| `engine_api` | `/api/v1` | JSON REST API. |
| `engine_cli` | `flask engine …` | Index management and ingestion commands. |
| `page` (legacy) | `/legacy` | Original Anna's Archive book/paper search. |
| `up` | `/up` | Health checks. |

Blueprints are registered defensively in `allthethings/app.py`: if an optional dependency is
missing, the platform logs a warning and the legacy app still boots.

## Hybrid retrieval

Each query runs two retrievers **in parallel** over the same documents:

1. **BM25** — a `multi_match` over `title^3`, `abstract^2`, `search_text`, `authors`, `equations`.
2. **kNN** — cosine similarity over the `embedding` dense-vector field, using the query's
   locally computed embedding.

Their ranked ID lists are merged with **Reciprocal Rank Fusion**:

```
score(d) = Σ_i  weight_i / (k + rank_i(d))
```

RRF needs only the *rank* of a document in each list, not calibrated scores, so lexical
relevance and cosine similarity — which live on different scales — combine cleanly. `k`
(default 60) flattens the contribution curve. The implementation
(`engine.search.reciprocal_rank_fusion`) is a pure function and is unit-tested without
Elasticsearch.

Modes: `hybrid` (default), `bm25` (lexical only), `semantic` (vector only). An empty query
browses the corpus by recency, filtered by facets.

Without a sentence-embedding model the vectors come from the deterministic hashing
fallback. Those are not semantic — unrelated tokens share hash buckets — so the vector
retriever is then restricted to documents sharing at least one query term
(`Embedder.semantic`; Postgres uses an any-term `tsquery`, Elasticsearch a `multi_match`
filter). It still adds recall where strict lexical matching finds nothing (Postgres
full-text search requires every query term), but it never puts an unrelated document on
screen, and `/health` reports `embedding: "hashing"` so clients do not label it
"semantic".

## What happens when you click Search

```
#search-form submit ─► app.js doSearch()                 (frontend/app.js)
   └─► runtime.search(request)                           (frontend/search-runtime.js)
         ├─ Live not ready yet? the request waits (connecting / waking) and
         │  fails with "unavailable" if Anna never answers — never answered by Demo
         └─► GET {API}/api/v1/search?q=…&mode=…         ({API}: frontend/config.js)
               └─► engine_api.search()                   (allthethings/engine_api/views.py)
                     └─► backend.get_search_service().search()   (engine/backend.py)
                           ├─ Postgres: engine/pg/search.py  (FTS + pgvector, RRF)
                           └─ Elasticsearch: engine/search.py (BM25 + kNN, RRF)
   ◄── results, facets, retrieval report ── renderResults(), renderTree()
   └─► runtime.summarize() ─► POST /api/v1/summarize ─► engine/summarize.py
         ◄── source excerpts with offsets ── the "Source report" panel
```

Every other workbench action takes the same route: **Details** → `GET /document/<id>` and
`/related`, **Compare** → `POST /compare`, **Save** → `/collections…`, **File ▸ Verify** →
`POST /evidence/verify`. The runtime is the only code that calls the backend, and Demo Mode
(the bundled three-document corpus in `frontend/demo-*.js`) answers only after the user
chooses it.

## The document model

Every source normalizes its raw records into one `Document`
(`engine/documents.py`). The same object is indexed into Elasticsearch and returned by search,
giving the whole platform one shape. Highlights:

- Stable, namespaced IDs: `Document.make_id(source, native_id)` → `"arxiv:<sha1>"`, so
  re-ingestion **updates** rather than duplicates.
- Automatic **equation** (LaTeX/MathML) and **code** detection.
- `search_text()` for BM25, `embedding_text()` (title + abstract + capped body) for vectors.

## The index

`engine/index.py` defines the `engineering_docs` index with both `text` fields (BM25) and a
`dense_vector` field (`dims` from config, `cosine` similarity, `index: true` for kNN). It uses
the `elasticsearch` client directly (not the Flask extension) so the same code path serves web
requests, Celery workers, and CLI ingestion.

## Ingestion pipeline

```
SourcePlugin.fetch()  →  SourcePlugin.normalize()  →  embed (batch)  →  bulk index
     (raw records)          (Document)               (vectors)        (Elasticsearch)
```

`IngestionPipeline` (`engine/ingest/pipeline.py`) batches embedding and bulk-indexing for
throughput, and never lets an embedding failure abort a crawl. See
[`PLUGINS.md`](PLUGINS.md) for the plugin contract.

## Graceful degradation

| Missing | Behavior |
|---|---|
| `sentence-transformers` / `torch` | Deterministic hashing embedder (search plumbing still works). |
| `pypdf` | PDF text extraction returns empty; documents indexed on metadata. |
| `sqlalchemy` | Collections API returns a storage-unavailable error; search unaffected. |
| Search backend down | API returns `503`; the workbench says "Anna's research backend isn't reachable", offers Retry, Diagnostics and Demo Mode, and retries in the background. |
| Free host asleep (cold start) | The workbench shows "Waking Anna's research backend…", queues the query, and runs it when the backend answers. |
| Local LLM off | Summaries use the extractive generator. |
