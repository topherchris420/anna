# REST API (v1)

All endpoints are under `/api/v1` and return JSON. There is no authentication; collection
ownership is a simple `owner` string (wire in real auth for production).

## Meta

### `GET /api/v1/health`
Engine and index status.
```json
{ "service":"vers3dynamics-engineering-intelligence", "engine_version":"0.1.0",
  "index":"engineering_docs", "index_exists":true, "document_count":1234,
  "elasticsearch":"ok", "embedding_model":"sentence-transformers/all-MiniLM-L6-v2" }
```

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
  "hits": [ { "score":0.032, "highlights":["…<em>kalman</em>…"],
             "document": { "id":"arxiv:…","title":"…","source":"arxiv", … } } ] }
```

### `GET /api/v1/document/<id>`
Fetch one document (full body). `404` if not found.

### `GET /api/v1/document/<id>/related`
Related-document recommendations via vector similarity (`?size=8`).

## Answers & comparison

### `POST /api/v1/summarize`
Citation-first answer. Provide a query; optionally pin specific document `ids`.
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
(`400` bad request, `404` not found, `503` when Elasticsearch/storage is unavailable).
Search errors return `503` with an empty `hits` array so clients can render gracefully.
