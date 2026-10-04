# REST API (v1)

All endpoints are under `/api/v1` and return JSON. There is no authentication; collection
ownership is a simple `owner` string (wire in real auth for production).

## Meta

### `GET /api/v1/health`
Engine and index status.
```json
{ "service":"vers3dynamics-engineering-intelligence", "engine_version":"0.1.0",
  "backend":"postgres", "index":"engineering_docs", "index_exists":true,
  "document_count":1234, "backend_status":"ok", "ready":true,
  "retrieval":"hybrid", "vector_search":true,
  "embedding_model":"sentence-transformers/all-MiniLM-L6-v2" }
```
Clients gate on `ready`. `retrieval` is `hybrid`, `fulltext-only` (Postgres without
pgvector) or `unavailable`; `backend_status` carries the error text when the
backend cannot be reached.

### `GET /api/v1/sources`
List registered ingestion sources with their metadata.

## Search

### `GET /api/v1/search`
Hybrid search with facets, filters, and paging.

| Param | Default | Description |
|---|---|---|
| `q` | – | Query text (empty = browse by recency). |
| `mode` | `hybrid` | `hybrid` \| `bm25` \| `semantic`. |
| `page`, `per_page` | `1`, `20` | Pagination (per_page ≤ 100). |
| `source` | – | Repeatable. Filter by source (`arxiv`, `github`, …). |
| `kind` | – | Repeatable. `paper`, `report`, `standard`, `repository`, `code`, `documentation`, `datasheet`. |
| `category` | – | Repeatable. Filter by category. |
| `language` | – | Repeatable. Natural or programming language. |
| `version` | – | Documentation version (e.g. `v5.1`). |
| `has_code`, `has_equations` | – | `true`/`false`. |
| `year_from`, `year_to` | – | Publication-year range. |

```bash
curl "http://localhost:8000/api/v1/search?q=kalman+filter&source=arxiv&has_equations=true"
```

Response:
```json
{ "query":"kalman filter", "mode":"hybrid", "total":42, "page":1, "per_page":20,
  "took_ms":18,
  "facets": { "source":[{"value":"arxiv","count":30}], "kind":[…], "categories":[…] },
  "retrieval": { "backend":"elasticsearch", "executed":["bm25","knn"], "fusion":"rrf",
                 "degraded":false, "unavailable":[], "score_ceiling":0.0328, … },
  "hits": [ { "score":0.0299, "relevance":0.9118, "highlights":["…<em>kalman</em>…"],
             "explanation": { "method":"rrf", "ranks":{"bm25":1,"knn":2}, "contributions":{…} },
             "document": { "id":"arxiv:…","title":"…","source":"arxiv", … } } ] }
```

Search responses also include `retrieval` (backend, requested mode, executed
retrievers, unavailable paths, embedding type, fusion strategy, count scope, and
`score_ceiling`, the highest fused score the executed retriever mix can assign).
Each hit includes `explanation` with per-retriever ranks and score contributions,
and `relevance` = `score / retrieval.score_ceiling` on a 0–1 scale, where 1.0
means ranked first by every retriever that ran. It is the same scale as the
agent endpoint's `relevance_score`, and a ranking share rather than a
probability of correctness. These are additive fields; `mode` continues to echo
the requested mode. Consult `retrieval.executed` for the paths that actually
ran. See [EVIDENCE.md](EVIDENCE.md).

### `POST /api/v1/agent/search`
Dedicated endpoint for LLM agent tool consumption (e.g. the james_library Rust
runtime): strict `{query, domain_filter, limit, min_score}` request, flat
citation-ready results with 0–1 relevance scores, errors in the same envelope.
See [AGENT_API.md](AGENT_API.md); the OpenAPI 3.1 spec is checked in at
[`openapi-agent-search.json`](openapi-agent-search.json) and served at
`GET /api/v1/agent/openapi.json`.

### `GET /api/v1/document/<id>`
Fetch one document (full body). `404` if not found.

### `GET /api/v1/document/<id>/related`
Related-document recommendations via vector similarity (`?size=8`).

## Answers & comparison

### `POST /api/v1/summarize`
Citation-first answer. Provide `q` as a nonempty string of at most 2,000
characters; optionally pin up to eight nonempty document `ids` (max 512 characters
each). Invalid JSON shapes/types and oversized requests return `400` before
retrieval. Repeated IDs are deduplicated.

Responses include `grounding` (`source-extract`, `references-only`, or
`insufficient-evidence`) and optional `fallback_reason`. Each returned citation
includes `excerpts`, with exact `quote`, `document_id`, `field`, `start`, `end`,
`offset_unit: "unicode-code-points"`, and `matched_terms`. Only sources used in the
answer are cited. No matching evidence produces an explicit refusal and an empty
citation list. Model outputs with invalid/uncited references fall back to source
excerpts. Reference checks do not verify factual entailment.
See [EVIDENCE.md](EVIDENCE.md) for limits and export details.
```bash
curl -X POST http://localhost:8000/api/v1/summarize \
  -H 'Content-Type: application/json' \
  -d '{"q":"how does the ESP32 DMA handle circular buffers?"}'
```
```json
{ "query":"…", "generator":"extractive",
  "answer":"Circular DMA lets the ADC sample continuously… [1] …offloads the CPU… [2]",
  "citations":[ {"n":1,"id":"espressif:…","title":"…","url":"…","source":"espressif"} ] }
```

### `POST /api/v1/compare`
Side-by-side comparison of two documents.
```bash
curl -X POST http://localhost:8000/api/v1/compare \
  -H 'Content-Type: application/json' -d '{"a":"arxiv:aaa","b":"github:bbb"}'
```

## Evidence

### `POST /api/v1/evidence/verify`
Re-check a saved research record against the current index. Send the packet the
workbench exports with **Save evidence .json** (`{captured_at, content_sha256,
record}`) or a bare `anna-research-record/v1` record. Nothing is stored.

The engine recomputes the record's SHA-256 fingerprint with the same canonical
JSON the browser used (`engine/records.py` is the byte-exact twin of
`frontend/evidence.js`), then re-reads every cited excerpt from the live index,
honouring the record's own `offset_unit`:

| `excerpts[].status` | Meaning |
|---|---|
| `verified` | The quote is at the recorded offsets of the current document field. |
| `relocated` | The quote still occurs in that field, at `found_at`; the offsets are stale. |
| `drifted` | The document and field exist but the quote is gone. |
| `missing-document` | The index no longer has this document id. |
| `missing-field` | The document has no such text field. |
| `invalid-excerpt` | The excerpt is malformed (`detail` says how); nothing was looked up. |

`ok` is true only when the fingerprint is not contradicted and every excerpt is
`verified`. A failed check is still a `200`; malformed input is `400`, a body
over 2 MB is `413`, and an unreachable index is `503`. At most 500 excerpts are
checked per request.

```bash
curl -X POST http://localhost:8000/api/v1/evidence/verify \
  -H 'Content-Type: application/json' --data-binary @anna-research.json
```
```json
{ "schema":"anna-research-record/v1", "schema_known":true,
  "fingerprint": { "declared":"3f2a…", "computed":"3f2a…", "matches":true },
  "checked_against": { "backend":"postgres", "index":"engineering_docs" },
  "excerpts": [ { "citation":1, "document_id":"espressif:…", "field":"abstract",
                  "start":0, "end":47, "offset_unit":"unicode-code-points",
                  "status":"verified" } ],
  "counts": { "verified":1, "relocated":0, "drifted":0, "missing-document":0,
              "missing-field":0, "invalid-excerpt":0 },
  "ok": true }
```

The same check runs from a shell as `flask engine verify-record anna-research.json`
(`--offline` for the fingerprint alone). See [EVIDENCE.md](EVIDENCE.md).

## Collections & bookmarks

Pass `owner` (query or body); defaults to `anonymous`.

| Method & path | Body / params | Purpose |
|---|---|---|
| `GET /collections?owner=` | – | List an owner's collections. |
| `POST /collections` | `{owner,name,description,is_public}` | Create a collection. |
| `GET /collections/<id>` | `?owner=` | Get a collection with its bookmarks. |
| `DELETE /collections/<id>` | `?owner=` | Delete a collection. |
| `POST /collections/<id>/bookmarks` | `{document_id,title,url,source,note}` | Add a bookmark (idempotent). |
| `DELETE /collections/<id>/bookmarks/<document_id>` | `?owner=` | Remove a bookmark. |

```bash
curl -X POST "http://localhost:8000/api/v1/collections?owner=me@x.com" \
  -H 'Content-Type: application/json' -d '{"name":"STM32 DMA refs"}'
```

## Error model

Errors return a JSON body with an `error` field and an appropriate status code
(`400` bad request, `404` not found, `413` payload too large, `503` when the
search backend or storage is unavailable).
Search errors return `503` with an empty `hits` array so clients can render gracefully.
