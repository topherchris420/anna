# Agent Search API — LLM tool contract

`POST /api/v1/agent/search` is a dedicated search endpoint for LLM agent tool
consumption (the [james_library](https://github.com/topherchris420/james_library)
Rust agent runtime is the first client). It trades the human-facing
`/api/v1/search` features — facets, paging, `<em>` highlight markup — for what
a model actually needs in its context window:

- a **strict four-field request** that maps 1:1 onto a function-calling schema,
- a **flat, citation-ready result list** (no nested `document` envelope),
- **plain-text chunks** capped at 1200 characters,
- **relevance scores normalized to 0–1** so agents can threshold and compare.

The machine-readable contract lives in
[`openapi-agent-search.json`](openapi-agent-search.json) (OpenAPI 3.1) and is
served live at `GET /api/v1/agent/openapi.json`. The Python source of truth is
`allthethings/engine_api/agent_spec.py`; a unit test keeps the checked-in copy
in sync.

## Request

`POST /api/v1/agent/search` with a JSON body:

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | string | yes | – | Search string or academic query. |
| `domain_filter` | string | no | – | Collection/topic/source filter, see below. |
| `limit` | integer | no | `5` | Max results, clamped to `1..25`. |
| `min_score` | number | no | `0.0` | Drop results below this normalized relevance, clamped to `0..1`. |

Out-of-range numbers are **clamped**, wrong types are **rejected** (`400`), and
unknown keys are ignored.

### `domain_filter` resolution

One string, resolved against the engine's facets in this order (first match
wins):

1. **Explicit prefix** — `source:arxiv`, `kind:paper`, `category:cs.RO`
   (alias `topic:`) pins the facet directly.
2. **Bare document kind** — `paper`, `report`, `standard`, `repository`,
   `code`, `documentation`, `datasheet`, `library`, `other` → kind filter.
3. **Bare registered source** — `arxiv`, `github`, `nasa`, `nist`, `ieee`,
   `doe`, `arm`, `stm32`, `espressif`, `riscv`, `linux_kernel`,
   `shadowlibraries` → source filter.
4. **Anything else** → category filter (e.g. arXiv's `cs.RO`). Unknown values
   match nothing rather than erroring, so agents can probe safely.

## Response

`200` — results are ranked best-first; `total_results` always equals
`results.length` (the post-filter count, not a corpus-wide total):

```json
{
  "status": "success",
  "query": "kalman filter divergence",
  "total_results": 1,
  "results": [
    {
      "id": "arxiv:8c4f01f9a3b2d715",
      "title": "Kalman Filter Divergence Analysis",
      "authors": ["A. Author", "B. Author"],
      "content_chunk": "…the Kalman gain saturates and the filter diverges…",
      "source_url": "https://arxiv.org/abs/2401.00001",
      "relevance_score": 0.92,
      "source": "arxiv",
      "kind": "paper",
      "published": "2024-01-15"
    }
  ]
}
```

- `content_chunk` — the retriever's matched passages when available, else the
  abstract, else the head of the body. Plain text (highlight markup stripped),
  ≤ 1200 chars.
- `source_url` — canonical link for citation; falls back to the PDF link and
  may be `""` when the source has neither.
- `relevance_score` — the hybrid (BM25 + semantic kNN) Reciprocal-Rank-Fusion
  score divided by its theoretical ceiling for the retrievers that ran. `1.0`
  means top-ranked by *every* retriever; a document found by only one of the
  two lands near `0.5` even at rank 1. Scores are comparable across queries.
- `source` / `kind` / `published` — additive provenance fields beyond the core
  contract; cheap in context, useful for citations.

Errors keep the same envelope so one client struct deserializes both:

```json
{ "status": "error", "error": "'query' is required and must be a non-empty string",
  "total_results": 0, "results": [] }
```

`400` malformed request · `503` search backend unavailable.

### Try it

```bash
curl -s -X POST http://localhost:8000/api/v1/agent/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"kalman filter divergence","domain_filter":"arxiv","limit":3,"min_score":0.2}'
```

## james_library client contract (Rust `reqwest` / `serde`)

The response is deliberately shaped so the whole wire format is two `serde`
structs — no enums, no polymorphic fields, `published` the only nullable:

```rust
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize)]
pub struct AgentSearchRequest {
    pub query: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub domain_filter: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub limit: Option<u32>, // server default 5, clamped to 1..=25
    #[serde(skip_serializing_if = "Option::is_none")]
    pub min_score: Option<f64>, // server default 0.0, clamped to 0..=1
}

#[derive(Debug, Deserialize)]
pub struct AgentSearchResponse {
    pub status: String, // "success" | "error"
    #[serde(default)]
    pub error: Option<String>, // set when status == "error"
    #[serde(default)]
    pub query: Option<String>,
    pub total_results: u32,
    pub results: Vec<AgentSearchResult>,
}

#[derive(Debug, Deserialize)]
pub struct AgentSearchResult {
    pub id: String,
    pub title: String,
    pub authors: Vec<String>,
    pub content_chunk: String,
    pub source_url: String,
    pub relevance_score: f64,
    pub source: String,
    pub kind: String,
    pub published: Option<String>,
}
```

Call it with the same reqwest conventions as the existing james_library tools
(`src/tools/weather_tool.rs` et al. — explicit timeouts, runtime proxy):

```rust
let response = client
    .post(format!("{base_url}/api/v1/agent/search"))
    .json(&AgentSearchRequest {
        query: query.to_string(),
        domain_filter,
        limit: Some(5),
        min_score: Some(0.0),
    })
    .send()
    .await?
    .json::<AgentSearchResponse>()
    .await?;
```

And the `Tool` impl (james_library `src/tools/traits.rs`) advertises the same
schema the OpenAPI file declares:

```rust
fn name(&self) -> &str { "anna_search" }

fn description(&self) -> &str {
    "Hybrid keyword+semantic search over the Anna engineering knowledge \
     index (papers, standards, code, datasheets). Returns citation-ready \
     snippets with 0-1 relevance scores."
}

fn parameters_schema(&self) -> serde_json::Value {
    // Mirror components.schemas.AgentSearchRequest from
    // docs/openapi-agent-search.json (or fetch /api/v1/agent/openapi.json
    // at startup and embed it verbatim).
    serde_json::json!({
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": { "type": "string", "description": "Search string or academic query." },
            "domain_filter": { "type": "string", "description": "Optional source/kind/category filter, e.g. 'arxiv', 'paper', 'category:cs.RO'." },
            "limit": { "type": "integer", "minimum": 1, "maximum": 25, "default": 5 },
            "min_score": { "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.0 }
        }
    })
}
```

Because `status`/`error` are part of the envelope, `execute()` can map any
non-`success` response straight onto `ToolResult { success: false, error, .. }`
without inspecting HTTP status codes.
