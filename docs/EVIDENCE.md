# Inspectable research records

Anna's workbench connects each search to the retrieval paths that actually ran,
then shows the source passages used in its report. Click **Why this result?**
to inspect ranks; click an answer's citation number to open its source excerpts.
The existing desktop interface, offline demo, and agent search contract remain
available.

## What a source report establishes

The default report selects exact passages from abstracts **and** document bodies.
Every excerpt includes the document ID, field, start/end offsets, and matching
query terms. Only sources used in the answer receive citations. Duplicate IDs
and repeated passages do not create additional evidence.

The selector inspects up to eight distinct documents, the first 24,000 characters
of each abstract/body, and passages of 21–1,000 characters. It selects at most five
passages by default and at most two per source, prioritizing source diversity.
English question/stop words do not count as matches; Unicode content terms do.
These are lexical relevance heuristics, not semantic entailment or a completeness
check. A paraphrase with no matching terms can be missed. A relevant-looking
passage may still fail to answer the question. The original source can be wrong.

Python API offsets count Unicode code points; demo offsets count JavaScript
UTF-16 code units. Each excerpt declares `offset_unit`. Verify the quote against
the indicated document field using the corresponding string slicing convention.
The human-facing answer displays any original `[number]` references as full-width
brackets to distinguish them from Anna's citation numbers; `excerpts[].quote`
preserves the original text exactly.

Summary `grounding` values:

| Value | Meaning |
|---|---|
| `source-extract` | Answer assembled from query-matching source passages. |
| `references-only` | Model prose passed conservative citation-syntax checks. Factual support has **not** been verified. |
| `insufficient-evidence` | No qualifying excerpts were selected. No sources are cited. |

When enabled, the local LLM receives only the selected passages. Every sentence
must end in known citation markers. Empty output, unavailable models, unknown
citations, or uncited sentences fall back to the source extracts. The response
reports `fallback_reason` (`model-unavailable` or `invalid-citations`). The
conservative syntax checker can reject valid prose containing abbreviations;
fallback remains available without model inference. A model can still make a
false claim with a valid citation number, which is why it is never labeled as
verified evidence.

## What retrieval details establish

`GET/POST /api/v1/search` now includes additive `retrieval` metadata and
`hits[].explanation`:

- `executed` reports successful paths: `bm25`, `fts`, `knn`, `browse`, or
  `demo-lexical`.
- `unavailable` and `degraded` disclose partial retrieval, including Postgres
  falling back to full-text search without pgvector. Elasticsearch encoding
  failures no longer discard healthy lexical results.
- `embedding` distinguishes `sentence-transformer` from the deterministic
  `hashing` fallback. Hashing vectors do **not** provide semantic understanding.
- `ranks` are one-based positions in each executed retriever; `contributions`
  reconstruct the unrounded fusion score. Multiple retrievers use
  `1 / (rrf_k + rank)`; a single retriever uses `1 / rank`.
- `candidate_count` and `total_scope` distinguish candidate windows from corpus
  counts. Candidate counts are not estimates of every relevant document.
- Rank scores are ordering signals, **not probabilities of correctness**.

Demo ranking remains weighted lexical scoring and is labeled accordingly.
A trained vector model, live database, or LLM is not required for the demo.

## Save a research record

After a search and its summary settle, use:

- **Save report .md** for a readable query, answer, citations, quoted passages,
  source links, and current-page result list.
- **Save evidence .json** for a versioned `anna-research-record/v1` snapshot:
  request/filters, provider, actual retrieval, result metadata and explanations,
  summary, and source excerpts.

Exports contain the **current result page**, not the entire corpus. They do not
include API endpoint configuration or browser storage. If summarization fails,
the available search can still be exported with `summary: null`. Starting or
clearing a query invalidates the previous export; a late response cannot replace
the current search or report.

The JSON wrapper contains `captured_at`, `content_sha256`, and `record`. The hash
covers UTF-8 JSON of `record` with recursively sorted object keys and preserved
array order (`EngineEvidence.canonical`). Timing and export time do not affect
it. The hash detects content differences; it is **not** a digital signature,
proof of source authenticity, or a promise that a future search will return the
same corpus. Keep the saved excerpts when the live index changes.

Verify an exported file with the same dependency-free module used by the UI:

```bash
node - <<'JS'
const fs = require('node:fs');
const { webcrypto } = require('node:crypto');
const evidence = require('./frontend/evidence.js');
const saved = JSON.parse(fs.readFileSync('anna-research.json', 'utf8'));
evidence.fingerprint(saved.record, webcrypto).then(hash => {
  if (hash !== saved.content_sha256) throw new Error('Record content changed');
  console.log('Record fingerprint matches');
});
JS
```

## Regression checks

```bash
python -m pytest -q -c test/engine/pytest-smoke.ini --noconftest
npm run test:frontend
npm run test:deploy
npm run build
npm --prefix frontend run build
```

Tests cover exact offsets, body-only evidence, unrelated-source refusal, duplicate
sources, citation failures, model fallback, retrieval contribution arithmetic,
encoder failures, malformed summary requests, Unicode, safe links, escaped
exports, immutable snapshots, and content fingerprint reproducibility. Database
retrieval uses test doubles in this suite; production index relevance still
requires evaluation on a representative labeled corpus.
