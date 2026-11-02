# Architecture

Anna · R.A.I.N. DataMatrix Engine (a Vers3Dynamics project) layers a hybrid, evidence-first
engineering search engine on top of the existing Elasticsearch + Flask architecture of Anna's
Archive. The two coexist: the legacy book/paper search remains registered under `/legacy`,
while the engine owns the root.

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
| `engine.evidence` | Deterministic source excerpts with exact offsets; citation-syntax checks. |
| `engine.retrieval` | Per-hit rank explanations and the shared 0–1 relevance scale. |
| `engine.records` | Portable research records: canonical JSON, fingerprints, verification (twin of `frontend/evidence.js`). |
| `engine.citations` | BibTeX entries and plain-text references for any document (twin of `frontend/citations.js`). |
| `engine.backend` / `engine.pg` | The Elasticsearch-or-Postgres facade, and the Postgres (FTS + pgvector) store and search service. |
| `engine.collections` | PostgreSQL/SQLite collections & bookmarks (lazy ORM). |
| `engine.ingest` | Modular, plugin-based ingestion pipeline. |
| `engine.tasks` | Celery `shared_task`s for background indexing. |

### 2. `allthethings/` — Flask integration

| Blueprint | Mount | Purpose |
|---|---|---|
| `engine_web` | `/` | Server-rendered pages (home, search, document, compare, collections, about), plus `/deck` and `/blueprint`. The same contract as the static workbench: outages are reported as outages (HTTP 503 with a notice), hits carry the shared relevance and their explanation, mode labels name what the backend runs, every document has a Cite panel. |
| `engine_api` | `/api/v1` | JSON REST API. |
| `engine_cli` | `flask engine …` | Index management and ingestion commands. |
| `page` (legacy) | `/legacy` | Original Anna's Archive book/paper search. |
| `up` | `/up` | Health checks. |

Blueprints are registered defensively in `allthethings/app.py`: if an optional dependency is
missing, the platform logs a warning and the legacy app still boots.

## Hybrid retrieval

Each query runs two retrievers **in parallel** over the same documents:

1. **Lexical** — the query's terms matched under one coverage rule (below): BM25 over
   `title^3`, `abstract^2`, `search_text`, `authors^2`, `equations^2` on Elasticsearch,
   IDF-weighted full-text search on Postgres.
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
fallback. Those are not semantic — unrelated tokens share hash buckets — so a **hybrid**
search then runs lexical retrieval alone (`engine.retrieval.vector_fusion_skipped`;
`retrieval.skipped: ["knn"]`): even gated to the lexical matches, hashed-term overlap only
re-ordered them, and measurably worse (nDCG@10 0.741 vs 0.726 on Postgres, 0.757 vs
0.738 on Elasticsearch — see [`test/relevance`](../test/relevance/README.md)). Asked for by
name, `semantic` mode still runs the term vectors, restricted to documents the lexical
rule admits, so they never put an unrelated document on screen; `/health` reports
`embedding: "hashing"` and `retrieval: "lexical"`, and clients label the modes
accordingly.

## Lexical matching

Both backends match a query the same way ([`engine/lexical.py`](../engine/lexical.py)):

- **Terms.** Each typed word is a term, `"a quoted phrase"` is one term that must appear as
  a phrase, and a leading `-` excludes a word or phrase. Stop words ("how", "do", "the" — the
  Snowball English list Postgres itself uses) are not terms, so "how do I estimate state
  with a kalman filter" asks for four. A hyphenated term ("RISC-V", "in-memory") matches its
  parts as a phrase, i.e. every spelling.
- **Coverage.** A document must carry `required_matches(ENGINE_LEXICAL_MIN_SHOULD_MATCH, n)`
  of the `n` terms — Elasticsearch's `minimum_should_match` notation, applied by both
  backends. The default, `70%`, admits either term of a two-term query, two of three or
  four, three of five. Requiring every term found nothing for most questions; requiring
  any one filled pages with documents that said "state".
- **Ranking.** Each carried term adds its weight, rare terms most: BM25 on Elasticsearch;
  on Postgres the BM25 IDF of the term times `ts_rank` for it alone, which saturates with
  repetition and weighs title over abstract over body. A multi-term query found verbatim
  as a phrase gets the phrase bonus (`ENGINE_LEXICAL_PHRASE_BOOST`) on both.
- **Analysis.** English stemming on both: Postgres builds `search_vector` with the
  `english` configuration, and Elasticsearch analyzes title, abstract, body and
  `search_text` with its `english` analyzer (`flask engine index-upgrade` rebuilds an index
  created before that, keeping its documents and vectors).
- **Explanation.** `retrieval.lexical` states the rule a search applied (terms, excluded
  terms, how many were required) and every hit's `explanation` names its
  `matched_terms` and `missing_terms`.

Each of these defaults was chosen on judged queries, and CI holds both real backends to
them — see [`test/relevance/README.md`](../test/relevance/README.md).

## Spelling

Coverage matching hides a typo: "reinforcment learning" still returns pages — about
*learning*. One slip in a query's key word took nDCG@10 from 0.75 to 0.55 on the judged
queries without emptying a single result page, so the engine corrects
([`engine/spelling.py`](../engine/spelling.py)), on both backends alike:

- **Which words.** A term no indexed document contains (its document frequency, which
  Postgres' lexical plan already counts and Elasticsearch learns for free from which named
  term clauses its hits matched), of five or more ASCII letters, outside quotes, not
  excluded, and **not an English word** — checked against ENABLE, a public-domain list
  ([`engine/data`](../engine/data/README.md)). In a small corpus most everyday words are
  absent; without that check "lunch robot motion planning" became "launch …".
- **Into what.** The indexed word the typed one most plausibly slipped from: one slip away
  among the words of the documents the rest of the query found; else the most common word
  one slip away anywhere in the index (one SQL round trip, or one Elasticsearch `filters`
  aggregation, over a few hundred candidates — 20–70 ms at 50,000 documents); else two
  slips away among those documents. A slip is a dropped, doubled or extra letter, a swap of
  neighbours, or a neighbouring key (or vowel); the first letter is taken as typed.
  An index stores stems, so the spelling shown is the one the documents use.
- **What the user sees.** The search runs for the corrected query, and the results say so:
  `spelling` in the API (`typed`, `query`, each correction), "Showing results for …
  Search instead for …" on both pages. `spell=false` searches exactly what was typed.

Measured on the judged queries misspelled by one slip and by two, CI holds both backends to
it, and to correcting none of the 47 queries as written — see
[`test/relevance/README.md`](../test/relevance/README.md#misspelled-queries).

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
chooses it. Citations are the exception that proves the rule: they are a pure function of a
document's fields, so the workbench builds them locally with `frontend/citations.js` — the
byte-exact twin of `engine/citations.py`, which serves `GET /document/<id>/citation` and
`flask engine cite` — and Live and Demo Mode cite identically, offline included.

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
