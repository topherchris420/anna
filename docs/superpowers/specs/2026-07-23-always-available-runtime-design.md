# Always-Available Search Runtime

**Date:** 2026-07-23  
**Status:** Approved design  
**Repository:** `topherchris420/anna`

## Summary

Anna's search engine is healthy when exercised in isolation, but the product
currently fails at its delivery boundaries. The static frontend depends on a
separately hosted API that may be asleep or unavailable, search requests have
no cancellation or stale-response protection, the UI can advertise retrieval
capabilities the backend does not actually have, and the documented static
build scripts rely on Unix shell commands.

This design adds an always-available runtime to the existing framework-free
frontend. The runtime prefers the full backend, falls back quickly to a
clearly labeled deterministic demo corpus, preserves the user's work while
reconnecting, and reports the retrieval capabilities that actually executed.
It also adds portable static assembly and a fast cross-platform CI lane.

The Python search engine remains authoritative for the full corpus. The demo
provider is a continuity layer, not a browser rewrite of the production
engine.

## Evidence Behind the Change

- `py -m pytest test/engine -q -c NUL --noconftest` passes with 142 tests and
  seven skips.
- The configured production API did not return a health response within 30
  seconds during the design audit.
- Both package manifests build with `rm`, `mkdir`, and `cp`, so `npm run build`
  fails on Windows.
- The static frontend has no automated test lane.
- `frontend/app.js` does not abort superseded search or summary requests, so a
  slow response can overwrite a newer query.
- PostgreSQL can operate without pgvector, but the static UI still offers and
  labels vector-backed modes as though they are available.

## Goals

1. Render a useful search experience even when the backend is cold, slow, or
   temporarily unreachable.
2. Make Live and Demo behavior explicit and truthful.
3. Prevent stale network responses from corrupting visible query state.
4. Preserve the existing retro workbench identity and URL-based query state.
5. Make static builds portable across Windows and Linux without dependencies.
6. Add fast, deterministic CI coverage for the product boundaries.
7. Keep the full backend and its public API backward compatible.

## Non-Goals

- Bundling or reproducing the full production corpus in the browser.
- Reimplementing BM25, vector search, RRF, ingestion, or collections in
  JavaScript.
- Guaranteeing a first-ever page load without any network access. The static
  application must still be loaded from a host or local filesystem/server.
- Adding a service worker, PWA lifecycle, or cache invalidation policy.
- Migrating the inherited `data-imports` package in this pass.
- Changing the visual design language or adopting a frontend framework.
- Masking malformed API responses or research errors as valid demo results.

## Chosen Approach

The approved approach is a resilient frontend shell with two providers:

```text
Existing UI renderer
        |
        v
SearchRuntime: connecting | live | demo | reconnecting
        |                              |
        v                              v
LiveProvider                    DemoProvider
/api/v1                         bundled corpus
```

The runtime owns provider selection, health probing, request cancellation,
capability normalization, and recovery. Both providers return the same minimal
search and summary shapes, so the existing result renderer remains the primary
presentation layer.

## Runtime State Model

The runtime has four public states:

- `connecting`: initial health probe or an explicit retry is active.
- `live`: the API health contract is valid and live requests may run.
- `demo`: the backend is unavailable or the user explicitly chose the bundled
  corpus.
- `reconnecting`: Demo Mode remains usable while background recovery probes
  run.

The selected provider and its capability object are mutable runtime state, not
inferred by individual UI components.

### Initial connection

1. Render the existing welcome content immediately.
2. Begin a health request with a short deadline.
3. Enter Live Mode when a valid health response arrives.
4. Enter Demo Mode on timeout, network failure, or an unavailable readiness
   state.
5. Preserve the configured API endpoint; fallback does not overwrite user
   settings.

### Recovery

Demo Mode remains interactive while reconnect attempts use a capped backoff.
Retries pause while `navigator.onLine` is false or the document is hidden.
When the API recovers, the current results are not silently replaced. The
status area announces that the full index is available and offers an explicit
switch to Live Mode with the current query and filters preserved.

Users can explicitly select Demo, retry Live, or update the endpoint through
the existing endpoint dialog.

## Provider Contract

Both providers expose:

```text
health() -> capability snapshot
search(search state, abort signal) -> search response
summarize(query, selected result context, abort signal) -> summary response
sources() -> source catalog
```

The browser runtime normalizes provider responses before they reach rendering
code. A response must contain the expected collection fields and finite paging
values. Invalid live responses are treated as provider failures, not empty
successful searches.

### Capability snapshot

The normalized capability object contains:

- provider: `live` or `demo`
- ready: boolean
- backend name
- retrieval: `hybrid`, `semantic`, `bm25`, or `demo-lexical`
- vector search availability
- document count when known
- a short user-facing status label

The backend health endpoint will return consistent `ready`, `retrieval`, and
`vector_search` fields for Elasticsearch and PostgreSQL. Existing fields remain
available.

If vector search is unavailable, the UI disables semantic mode and relabels the
active capability accurately. A deep link requesting an unsupported mode is
normalized to BM25 with an accessible notice.

## Request Concurrency

The runtime owns one generation counter and separate abort controllers for
health, search, summary, and source-catalog requests.

- Starting a search increments the generation and aborts the prior search and
  summary.
- A response may update the UI only if its generation is still current.
- Aborted requests do not render connection errors.
- Network failures and server `503` responses can trigger Demo Mode.
- Request validation errors and other client errors remain visible and do not
  silently change providers.

This prevents slow searches and summaries from overwriting newer work.

## Demo Corpus and Retrieval

The bundled corpus is a small set of repository-owned sample records derived
from existing offline demo material. Every record has:

- stable ID
- title
- abstract or short body
- source and kind
- canonical URL when one is already part of the sample
- authors, publication date, categories, and code/equation flags when known

The corpus is deliberately compact and labeled `Bundled demo data` throughout
the interface.

Demo retrieval is a pure deterministic function:

1. Normalize the query into lowercase alphanumeric terms.
2. Score title matches above abstract/body matches.
3. Add small deterministic boosts for category, source, and exact-phrase
   matches.
4. Apply the same source, kind, category, language, code, and equation filters
   used by the current UI.
5. Sort by score, then stable document ID for ties.
6. Produce facets, total count, and pagination from the filtered set.

Demo Mode exposes one `Demo lexical` retrieval mode. It does not claim BM25,
semantic search, RRF, or corpus-wide coverage.

### Demo summaries

The demo summarizer is extractive:

1. Select query-overlapping sentences from the visible top results.
2. Attach each sentence to its source record.
3. Emit only citation markers that map to returned citations.
4. Refuse with a concise `The bundled demo sources do not answer this query`
   message when no evidence overlaps the query.

No generated claim may exist without a citation to a bundled record.

## User Interface

The existing workbench shell, menus, result windows, filters, and typography
remain.

Changes are limited to:

- a persistent Live or Demo indicator in the status bar;
- a concise Demo Mode explanation near the welcome/results area;
- Retry Live, Switch to Live, and Use Demo controls in the endpoint dialog;
- disabled retrieval modes when capabilities do not support them;
- an `aria-live` connection announcement for mode changes and recovery;
- specific timeout, offline, unavailable, and invalid-response messages.

Demo fallback does not clear the query, filters, page, endpoint configuration,
or scroll position unnecessarily.

## File Boundaries

- `frontend/demo-corpus.js`
  - bundled immutable sample records
- `frontend/demo-search.js`
  - pure deterministic search, facets, pagination, and extractive summaries
- `frontend/search-runtime.js`
  - providers, health deadlines, capability normalization, cancellation,
    retries, and mode transitions
- `frontend/app.js`
  - UI orchestration and rendering over the runtime
- `frontend/index.html`
  - script loading, status controls, and accessible announcements
- `frontend/styles.css`
  - small state/status additions within the existing design system
- `frontend/build.mjs`
  - cross-platform static asset assembly
- `allthethings/engine_api/views.py`
  - consistent health capability fields
- `.github/workflows/ci.yml`
  - fast cross-platform product smoke lane
- `test/frontend/`
  - dependency-free Node tests
- `test/engine/`
  - health capability regression tests

The exact implementation plan may consolidate files when a smaller boundary is
clearer, but pure demo retrieval and runtime/network state must remain separate
from DOM rendering.

## Build Design

`frontend/build.mjs` uses `node:fs/promises` and `node:path` to:

1. validate the static asset manifest;
2. create or clean the requested output directory safely;
3. copy the complete frontend asset set;
4. fail when a required asset is missing;
5. print the resolved output path and copied asset count.

The root package builds `frontend/` into `dist/`. The frontend package builds
the same source set into `frontend/build/`. Both call the same script and work
on Windows and Linux.

No shell-specific `rm`, `mkdir`, or `cp` remains in package scripts.

## Error Handling

Errors are separated into:

- timeout;
- browser offline;
- network/unreachable;
- backend unavailable/not ready;
- invalid response;
- client/request error.

Only availability failures trigger automatic Demo Mode. Invalid user requests
remain errors. Raw server exceptions are logged for development but reduced to
safe, concise user-facing messages.

The reconnect loop has one active timer, one active health request, a maximum
delay, and visibility/online guards. It is stopped when Live Mode is selected
or the page unloads.

## Testing

### Node unit tests

Using `node:test` and `node:assert` only:

- ranking is deterministic;
- title, phrase, and body weighting order is stable;
- filters and facets match the visible corpus;
- pagination and tie-breaking are stable;
- every summary citation marker resolves to one returned citation;
- unsupported questions produce the evidence refusal;
- timeout and cancellation do not mutate current state;
- stale generations cannot publish results;
- recovery preserves the query and requires an explicit Live switch.

### Static assembly tests

- root and frontend builds succeed on Windows and Linux;
- required output files exist;
- stale output files are removed;
- a missing manifest asset fails the build.

### Python regression tests

- health reports truthful readiness and retrieval capabilities for both
  backends;
- PostgreSQL without pgvector reports full-text-only behavior;
- existing API fields remain compatible;
- the existing isolated engine test suite remains green.

### Browser QA

- healthy Live Mode;
- forced timeout and immediate Demo Mode;
- useful demo search, filters, paging, and cited summary;
- background recovery and explicit switch to Live;
- rapidly superseded queries;
- offline/online browser events;
- endpoint changes;
- mobile layout;
- keyboard navigation and accessible connection announcements.

## CI

The existing Docker-oriented test lane remains. A new fast smoke job runs on
both Ubuntu and Windows with Python and Node:

1. install the minimal test dependencies;
2. run isolated engine tests;
3. run frontend Node tests;
4. build from the repository root;
5. build from `frontend/`;
6. assert the expected static outputs.

This lane is intended to fail in minutes and protect the exact boundaries that
currently regress unnoticed.

## Rollout and Compatibility

- The live API remains the preferred provider.
- Existing query-string and local-storage endpoint overrides remain valid.
- Existing search and summary endpoints remain backward compatible.
- Demo records are versioned with the static assets and require no runtime
  download.
- The mode indicator prevents bundled results from being mistaken for the full
  index.
- No dependency or index migration is required.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Demo results are mistaken for production search | Persistent Demo label, lexical-only mode name, bundled document count |
| Local ranking drifts from backend semantics | Do not call it BM25/RRF; keep provider contract shared but capability labels distinct |
| Recovery replaces work unexpectedly | Require explicit switch to Live and preserve query/filter state |
| Reconnect loop causes request churn | One timer/request, capped backoff, visibility and online guards |
| New runtime increases frontend complexity | Keep providers pure and separate from existing DOM renderer; dependency-free unit tests |
| Static asset list becomes stale | Manifest validation and build smoke tests |

## Acceptance Criteria

1. A visitor can search and receive cited demo results when the API is
   unreachable.
2. The interface identifies Demo Mode unambiguously.
3. Backend recovery is detected without replacing current results.
4. Superseded live requests cannot overwrite a newer query.
5. Unsupported vector modes are not advertised.
6. Both documented static builds run on Windows and Linux.
7. The new smoke CI covers engine, frontend runtime, and static assembly.
8. Existing engine tests pass.
9. No new runtime or development dependency is added.
10. Full-corpus claims remain exclusive to Live Mode.

