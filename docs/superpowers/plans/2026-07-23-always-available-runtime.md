# Always-Available Search Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Anna immediately useful when its full search backend is cold or unavailable by adding a truthful deterministic Demo Mode, safe request orchestration, portable builds, and fast cross-platform verification.

**Architecture:** Preserve the existing framework-free UI renderer and place a small `SearchRuntime` between it and two providers: the existing `/api/v1` backend and a bundled deterministic demo provider. The runtime owns health probing, capability normalization, provider transitions, cancellation, stale-response protection, and recovery; both providers return the current API response shapes.

**Tech Stack:** Browser JavaScript (ES5-compatible application code with dependency-free UMD helpers), Node.js 20 built-ins (`node:test`, `node:assert`, `node:fs/promises`), Python 3.10+, Flask 2.2, pytest 7, GitHub Actions.

## Global Constraints

- Add no runtime or development dependency.
- Keep the Python backend authoritative for the full corpus.
- Label bundled results as Demo Mode and never call demo retrieval BM25, semantic, hybrid, vector, or RRF.
- Preserve existing query-string state, endpoint overrides, and retro workbench styling.
- Keep `/api/v1` search and summary responses backward compatible.
- Do not add a service worker, PWA cache, frontend framework, browser-side vector index, or legacy `data-imports` migration.
- Only availability failures may trigger automatic Demo Mode; client/request errors remain visible.
- Backend recovery must not replace the current result set without an explicit user action.
- Every demo summary sentence must map to a returned citation.
- Build output deletion must be restricted to the resolved `dist/` or `frontend/build/` target.
- Use TDD for every behavior change and keep task commits independently reviewable.

## Planned File Structure

**Create**

- `frontend/build.mjs` — one cross-platform static asset assembler.
- `frontend/demo-corpus.js` — immutable browser copy of the existing offline demo records.
- `frontend/demo-search.js` — pure deterministic demo search/facet/summary provider.
- `frontend/search-runtime.js` — live provider, runtime state machine, cancellation, and recovery.
- `test/frontend/build.test.mjs` — portable assembly tests.
- `test/frontend/demo-search.test.js` — deterministic demo-provider tests.
- `test/frontend/search-runtime.test.js` — runtime and live-provider tests.
- `test/frontend/static-contract.test.js` — DOM/source contract checks without jsdom.
- `test/engine/test_api_health.py` — capability and readiness API tests.
- `test/engine/pytest-smoke.ini` — OS-independent isolated-engine pytest config.

**Modify**

- `package.json` — portable build and frontend smoke scripts.
- `frontend/package.json` — the same build/test entrypoints from the subdirectory.
- `frontend/index.html` — runtime scripts, status badge, recovery action, and live region.
- `frontend/app.js` — route search, summaries, sources, health, and endpoint controls through the runtime.
- `frontend/styles.css` — small Live/Demo/recovery status styles and accessible utility class.
- `allthethings/engine_api/views.py` — consistent health capability fields.
- `.github/workflows/ci.yml` — add the fast Windows/Linux product-smoke lane.
- `README.md` — explain Live/Demo continuity and portable checks.
- `frontend/README.md` — document runtime modes and cross-platform commands.

---

### Task 1: Cross-Platform Static Assembly

**Files:**

- Create: `frontend/build.mjs`
- Create: `test/frontend/build.test.mjs`
- Modify: `package.json`
- Modify: `frontend/package.json`

**Interfaces:**

- Consumes: the existing static source directory `frontend/`.
- Produces:
  - `STATIC_ASSETS: readonly string[]`
  - `buildStatic(outputPath: string): Promise<{ outputDir: string; copied: number }>`
  - guarded CLI forms `node frontend/build.mjs dist` and, from `frontend/`,
    `node build.mjs build`

- [ ] **Step 1: Write the failing build tests**

Create `test/frontend/build.test.mjs`:

```js
import assert from "node:assert/strict";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { STATIC_ASSETS, buildStatic } from "../../frontend/build.mjs";

const REPO_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  ".."
);

test("buildStatic copies every declared asset and removes stale output", async () => {
  const output = path.join(REPO_ROOT, "dist");
  await mkdir(output, { recursive: true });
  await writeFile(path.join(output, "stale.txt"), "stale", "utf8");

  const result = await buildStatic(output);

  assert.equal(result.outputDir, path.resolve(output));
  assert.equal(result.copied, STATIC_ASSETS.length);
  await assert.rejects(stat(path.join(output, "stale.txt")));
  for (const asset of STATIC_ASSETS) {
    const body = await readFile(path.join(output, asset));
    assert.ok(body.length > 0, asset + " should not be empty");
  }
});

test("buildStatic refuses to replace the frontend source directory", async () => {
  const source = path.join(REPO_ROOT, "frontend");
  await assert.rejects(
    buildStatic(source),
    /refusing to replace the frontend source directory/i
  );
});

test("buildStatic refuses every output except repo dist or frontend build", async () => {
  await assert.rejects(
    buildStatic(REPO_ROOT),
    /output must be repository dist or frontend build/i
  );
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
node --test test/frontend/build.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `frontend/build.mjs`.

- [ ] **Step 3: Implement the portable builder**

Create `frontend/build.mjs`:

```js
import {
  copyFile,
  mkdir,
  rm,
  stat,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SOURCE_DIR = path.dirname(fileURLToPath(import.meta.url));

export const STATIC_ASSETS = Object.freeze([
  "index.html",
  "styles.css",
  "app.js",
  "config.js",
  "favicon.svg",
]);

function assertSafeOutput(outputDir) {
  const resolved = path.resolve(outputDir);
  if (resolved === SOURCE_DIR) {
    throw new Error("Refusing to replace the frontend source directory");
  }
  const allowed = [
    path.resolve(SOURCE_DIR, "..", "dist"),
    path.resolve(SOURCE_DIR, "build"),
  ];
  if (allowed.indexOf(resolved) < 0) {
    throw new Error(
      "Build output must be repository dist or frontend build"
    );
  }
  return resolved;
}

export async function buildStatic(outputPath) {
  if (!outputPath || typeof outputPath !== "string") {
    throw new Error("An output directory is required");
  }
  const outputDir = assertSafeOutput(outputPath);
  for (const asset of STATIC_ASSETS) {
    const source = path.join(SOURCE_DIR, asset);
    const info = await stat(source);
    if (!info.isFile()) {
      throw new Error(`Required static asset is not a file: ${asset}`);
    }
  }

  await rm(outputDir, { recursive: true, force: true });
  await mkdir(outputDir, { recursive: true });
  for (const asset of STATIC_ASSETS) {
    await copyFile(path.join(SOURCE_DIR, asset), path.join(outputDir, asset));
  }
  return { outputDir, copied: STATIC_ASSETS.length };
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = await buildStatic(process.argv[2]);
  console.log(`Built ${result.copied} static assets into ${result.outputDir}`);
}
```

Replace the root `package.json` scripts with:

```json
{
  "scripts": {
    "build": "node frontend/build.mjs dist"
  }
}
```

Replace the `frontend/package.json` scripts with:

```json
{
  "scripts": {
    "build": "node build.mjs build"
  }
}
```

Keep the existing names, versions, `private`, and descriptions unchanged.

- [ ] **Step 4: Run the focused tests and both builds**

Run:

```powershell
node --test test/frontend/build.test.mjs
npm run build
npm --prefix frontend run build
```

Expected:

- Node tests: 3 passed.
- Root build reports five files under `dist/`.
- Frontend build reports five files under `frontend/build/`.

- [ ] **Step 5: Commit Task 1**

```powershell
git add frontend/build.mjs test/frontend/build.test.mjs package.json frontend/package.json
git commit -m "Make static assembly portable across development hosts" `
  -m "Replace shell-specific file operations with a guarded Node built-in assembler shared by root and frontend builds." `
  -m "Constraint: No new package dependency" `
  -m "Rejected: rimraf and copyfiles because built-in filesystem APIs cover the required behavior" `
  -m "Confidence: high" `
  -m "Scope-risk: narrow" `
  -m "Tested: node build tests; root build; frontend build"
```

---

### Task 2: Deterministic Demo Corpus and Provider

**Files:**

- Create: `frontend/demo-corpus.js`
- Create: `frontend/demo-search.js`
- Create: `test/frontend/demo-search.test.js`
- Modify: `frontend/build.mjs`

**Interfaces:**

- Consumes: the three existing `_demo_documents()` records from `allthethings/engine_cli/views.py`.
- Produces:
  - global/CommonJS `EngineDemoCorpus: readonly DemoDocument[]`
  - global/CommonJS `EngineDemoSearch`
  - `search(corpus, request): SearchResponse`
  - `summarize(corpus, query, documentIds): SummaryResponse`
  - `createProvider(corpus): DemoProvider`

- [ ] **Step 1: Write failing deterministic-provider tests**

Create `test/frontend/demo-search.test.js`:

```js
const assert = require("node:assert/strict");
const test = require("node:test");

const corpus = require("../../frontend/demo-corpus.js");
const demo = require("../../frontend/demo-search.js");

function request(q, overrides) {
  return Object.assign(
    { q, mode: "bm25", page: 1, per_page: 20, filters: {} },
    overrides || {}
  );
}

test("DMA query deterministically ranks the ESP32 documentation first", () => {
  const first = demo.search(corpus, request("DMA circular buffer"));
  const second = demo.search(corpus, request("DMA circular buffer"));
  assert.deepEqual(first, second);
  assert.equal(first.mode, "demo-lexical");
  assert.equal(first.hits[0].document.id, "espressif:1020b2afe9f87462");
});

test("kind filters and facets describe only the filtered demo result set", () => {
  const result = demo.search(
    corpus,
    request("real time", { filters: { kind: ["repository"] } })
  );
  assert.equal(result.total, 1);
  assert.equal(result.hits[0].document.id, "github:dd47b427bee571ef");
  assert.deepEqual(result.facets.kind, [{ value: "repository", count: 1 }]);
});

test("pagination uses score then stable id for deterministic ordering", () => {
  const all = demo.search(corpus, request("real time", { per_page: 1 }));
  const page2 = demo.search(
    corpus,
    request("real time", { page: 2, per_page: 1 })
  );
  assert.equal(all.total, 2);
  assert.notEqual(all.hits[0].document.id, page2.hits[0].document.id);
});

test("every demo summary marker resolves to exactly one returned citation", () => {
  const result = demo.search(corpus, request("DMA circular buffers"));
  const summary = demo.summarize(
    corpus,
    "DMA circular buffers",
    result.hits.map((hit) => hit.document.id)
  );
  const markers = Array.from(summary.answer.matchAll(/\[(\d+)\]/g)).map(
    (match) => Number(match[1])
  );
  assert.ok(markers.length > 0);
  assert.deepEqual(
    Array.from(new Set(markers)).sort(),
    summary.citations.map((citation) => citation.n)
  );
  assert.equal(summary.generator, "demo-extractive");
});

test("unsupported questions refuse instead of manufacturing an answer", () => {
  const summary = demo.summarize(corpus, "photosynthesis chlorophyll", []);
  assert.equal(summary.citations.length, 0);
  assert.match(summary.answer, /bundled demo sources do not answer/i);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
node --test test/frontend/demo-search.test.js
```

Expected: FAIL because `frontend/demo-corpus.js` does not exist.

- [ ] **Step 3: Add the immutable demo corpus**

Create `frontend/demo-corpus.js` as a dependency-free UMD value:

```js
(function (root, factory) {
  "use strict";
  var corpus = factory();
  if (typeof module === "object" && module.exports) module.exports = corpus;
  root.EngineDemoCorpus = corpus;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  function freezeRecord(record) {
    Object.keys(record).forEach(function (key) {
      if (Array.isArray(record[key])) Object.freeze(record[key]);
    });
    return Object.freeze(record);
  }
  return Object.freeze([
    freezeRecord({
      id: "arxiv:c4694ed857f0ca8f",
      source: "arxiv",
      kind: "paper",
      title: "Model Predictive Control of Quadrotor UAVs",
      abstract:
        "We present a real-time model predictive control (MPC) scheme for " +
        "quadrotor trajectory tracking, solving a constrained QP at 100 Hz.",
      body: "",
      authors: ["A. Researcher", "B. Engineer"],
      published: "2023-05-01",
      updated: null,
      version: null,
      categories: ["eess.SY"],
      tags: [],
      language: "en",
      identifiers: {},
      has_equations: false,
      has_code: false,
      popularity: 0,
      url: "https://arxiv.org/abs/demo-1",
      pdf_url: "",
    }),
    freezeRecord({
      id: "github:dd47b427bee571ef",
      source: "github",
      kind: "repository",
      title: "demo/tiny-rtos",
      abstract:
        "A minimal preemptive real-time operating system for ARM Cortex-M.",
      body: "",
      authors: [],
      published: null,
      updated: null,
      version: null,
      categories: [],
      tags: ["rtos", "cortex-m", "embedded"],
      language: "C",
      identifiers: {},
      has_equations: false,
      has_code: true,
      popularity: 1200,
      url: "https://github.com/demo/tiny-rtos",
      pdf_url: "",
    }),
    freezeRecord({
      id: "espressif:1020b2afe9f87462",
      source: "espressif",
      kind: "documentation",
      title: "ESP32 DMA and Circular Buffers",
      abstract:
        "The ESP32 DMA engine supports circular (ping-pong) buffers for " +
        "continuous ADC and I2S data acquisition without CPU intervention.",
      body: "",
      authors: [],
      published: null,
      updated: null,
      version: "v5.1",
      categories: [],
      tags: ["esp32", "dma"],
      language: "en",
      identifiers: {},
      has_equations: false,
      has_code: false,
      popularity: 0,
      url: "https://docs.espressif.com/projects/esp-idf/en/v5.1/esp32/",
      pdf_url: "",
    }),
  ]);
});
```

- [ ] **Step 4: Implement pure demo search and summaries**

Create `frontend/demo-search.js` with this UMD surface and deterministic
algorithm:

```js
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.EngineDemoSearch = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var MULTI_FILTERS = ["source", "kind", "category", "language"];
  var REFUSAL =
    "The bundled demo sources do not answer this query. Switch to Live Mode " +
    "to search the full index.";

  function tokens(value) {
    return String(value || "").toLowerCase().match(/[a-z0-9]+/g) || [];
  }

  function includesAny(selected, values) {
    if (!selected || !selected.length) return true;
    return selected.some(function (item) {
      return values.indexOf(String(item).toLowerCase()) >= 0;
    });
  }

  function matchesFilters(doc, filters) {
    filters = filters || {};
    for (var i = 0; i < MULTI_FILTERS.length; i += 1) {
      var key = MULTI_FILTERS[i];
      var values =
        key === "category"
          ? doc.categories || []
          : [doc[key] == null ? "" : String(doc[key])];
      values = values.map(function (value) {
        return String(value).toLowerCase();
      });
      if (!includesAny(filters[key], values)) return false;
    }
    if (filters.has_code === "true" && !doc.has_code) return false;
    if (filters.has_equations === "true" && !doc.has_equations) return false;
    return true;
  }

  function countMatches(haystack, queryTerms) {
    var values = tokens(haystack);
    return queryTerms.reduce(function (count, term) {
      return count + values.filter(function (value) {
        return value === term;
      }).length;
    }, 0);
  }

  function scoreDocument(doc, query, queryTerms) {
    if (!queryTerms.length) return 1;
    var lowerQuery = query.toLowerCase();
    var title = String(doc.title || "").toLowerCase();
    var abstract = String(doc.abstract || "").toLowerCase();
    var tags = (doc.tags || []).concat(doc.categories || []).join(" ");
    var score =
      countMatches(title, queryTerms) * 8 +
      countMatches(abstract, queryTerms) * 3 +
      countMatches(tags, queryTerms) * 2 +
      countMatches(doc.source + " " + doc.kind, queryTerms);
    if (title.indexOf(lowerQuery) >= 0) score += 12;
    else if (abstract.indexOf(lowerQuery) >= 0) score += 5;
    return score;
  }

  function facet(rows, key) {
    var counts = Object.create(null);
    rows.forEach(function (row) {
      var values =
        key === "category" ? row.doc.categories || [] : [row.doc[key]];
      values.forEach(function (value) {
        if (value == null || value === "") return;
        counts[value] = (counts[value] || 0) + 1;
      });
    });
    return Object.keys(counts)
      .sort()
      .map(function (value) {
        return { value: value, count: counts[value] };
      });
  }

  function search(corpus, request) {
    request = request || {};
    var query = String(request.q || "").trim();
    var queryTerms = tokens(query);
    var page = Math.max(1, Number(request.page) || 1);
    var perPage = Math.max(1, Math.min(100, Number(request.per_page) || 20));
    var rows = corpus
      .filter(function (doc) {
        return matchesFilters(doc, request.filters);
      })
      .map(function (doc) {
        return { doc: doc, score: scoreDocument(doc, query, queryTerms) };
      })
      .filter(function (row) {
        return !queryTerms.length || row.score > 0;
      })
      .sort(function (a, b) {
        return b.score - a.score || a.doc.id.localeCompare(b.doc.id);
      });
    var start = (page - 1) * perPage;
    return {
      query: query,
      mode: "demo-lexical",
      total: rows.length,
      page: page,
      per_page: perPage,
      took_ms: 0,
      facets: {
        source: facet(rows, "source"),
        kind: facet(rows, "kind"),
        category: facet(rows, "category"),
        language: facet(rows, "language"),
        has_code: [
          {
            value: true,
            count: rows.filter(function (row) {
              return row.doc.has_code;
            }).length,
          },
        ],
        has_equations: [
          {
            value: true,
            count: rows.filter(function (row) {
              return row.doc.has_equations;
            }).length,
          },
        ],
      },
      hits: rows.slice(start, start + perPage).map(function (row) {
        return {
          score: row.score,
          highlights: row.doc.abstract ? [row.doc.abstract] : [],
          document: row.doc,
        };
      }),
    };
  }

  function sentences(text) {
    return String(text || "")
      .replace(/\s+/g, " ")
      .split(/(?<=[.!?])\s+/)
      .filter(function (sentence) {
        return sentence.length > 20;
      });
  }

  function summarize(corpus, query, documentIds) {
    var queryTerms = tokens(query);
    var byId = Object.create(null);
    corpus.forEach(function (doc) {
      byId[doc.id] = doc;
    });
    var chosen = [];
    (documentIds || []).forEach(function (id) {
      var doc = byId[id];
      if (!doc) return;
      sentences(doc.abstract || doc.body).forEach(function (sentence) {
        var overlap = countMatches(sentence, queryTerms);
        if (overlap > 0) chosen.push({ doc: doc, sentence: sentence, overlap: overlap });
      });
    });
    chosen.sort(function (a, b) {
      return b.overlap - a.overlap || a.doc.id.localeCompare(b.doc.id);
    });
    chosen = chosen.slice(0, 4);
    if (!chosen.length) {
      return {
        query: query,
        answer: REFUSAL,
        generator: "demo-extractive",
        citations: [],
      };
    }
    var citations = [];
    var numberById = Object.create(null);
    var answer = chosen
      .map(function (item) {
        if (!numberById[item.doc.id]) {
          numberById[item.doc.id] = citations.length + 1;
          citations.push({
            n: citations.length + 1,
            id: item.doc.id,
            title: item.doc.title,
            url: item.doc.url || item.doc.pdf_url,
            source: item.doc.source,
          });
        }
        return item.sentence + " [" + numberById[item.doc.id] + "]";
      })
      .join(" ");
    return {
      query: query,
      answer: answer,
      generator: "demo-extractive",
      citations: citations,
    };
  }

  function createProvider(corpus) {
    return {
      health: function () {
        return Promise.resolve({
          ready: true,
          provider: "demo",
          backend: "bundled",
          retrieval: "demo-lexical",
          vector_search: false,
          document_count: corpus.length,
          label: "Demo · " + corpus.length + " bundled documents",
        });
      },
      search: function (request) {
        return Promise.resolve(search(corpus, request));
      },
      summarize: function (request) {
        return Promise.resolve(
          summarize(corpus, request.query, request.documentIds)
        );
      },
      sources: function () {
        var names = Array.from(
          new Set(
            corpus.map(function (doc) {
              return doc.source;
            })
          )
        ).sort();
        return Promise.resolve({
          sources: names.map(function (name) {
            return { name: name, display_name: name + " (demo)" };
          }),
        });
      },
    };
  }

  return {
    createProvider: createProvider,
    search: search,
    summarize: summarize,
    tokens: tokens,
  };
});
```

Add `demo-corpus.js` and `demo-search.js` to `STATIC_ASSETS` in
`frontend/build.mjs`.

- [ ] **Step 5: Run focused tests and build validation**

Run:

```powershell
node --test test/frontend/demo-search.test.js test/frontend/build.test.mjs
npm run build
```

Expected: 8 tests pass; root build reports seven copied assets.

- [ ] **Step 6: Commit Task 2**

```powershell
git add frontend/demo-corpus.js frontend/demo-search.js frontend/build.mjs test/frontend/demo-search.test.js
git commit -m "Keep search useful when the full corpus is unavailable" `
  -m "Add a deterministic, citation-safe demo provider using the repository's existing offline sample records." `
  -m "Constraint: Demo retrieval must remain visibly distinct from production BM25 and vector search" `
  -m "Rejected: Browser vector index because it duplicates the authoritative engine" `
  -m "Confidence: high" `
  -m "Scope-risk: moderate" `
  -m "Directive: Every demo summary marker must resolve to a returned citation" `
  -m "Tested: deterministic ranking, filters, pagination, refusal, citation integrity, static build"
```

---

### Task 3: Search Runtime, Cancellation, and Recovery

**Files:**

- Create: `frontend/search-runtime.js`
- Create: `test/frontend/search-runtime.test.js`
- Modify: `frontend/build.mjs`

**Interfaces:**

- Consumes:
  - `DemoProvider` from Task 2.
  - `getBaseUrl(): string`.
- Produces global/CommonJS `EngineSearchRuntime`:
  - `ProviderError(code: string, message: string, status?: number)`
  - `createLiveProvider(options): LiveProvider`
  - `createRuntime(options): SearchRuntime`
  - `normalizeCapabilities(body, provider): CapabilitySnapshot`

- [ ] **Step 1: Write failing runtime tests**

Create `test/frontend/search-runtime.test.js`:

```js
const assert = require("node:assert/strict");
const test = require("node:test");

const runtimeApi = require("../../frontend/search-runtime.js");

function provider(overrides) {
  return Object.assign(
    {
      health: () =>
        Promise.resolve({
          ready: true,
          backend: "postgres",
          retrieval: "hybrid",
          vector_search: true,
          document_count: 3,
        }),
      search: (request) => Promise.resolve({ query: request.q, hits: [] }),
      summarize: () =>
        Promise.resolve({ answer: "", citations: [], generator: "test" }),
      sources: () => Promise.resolve({ sources: [] }),
    },
    overrides || {}
  );
}

test("healthy startup enters live mode with normalized capabilities", async () => {
  const runtime = runtimeApi.createRuntime({
    liveProvider: provider(),
    demoProvider: provider(),
    retryDelays: [],
  });
  await runtime.start();
  assert.equal(runtime.getSnapshot().phase, "live");
  assert.equal(runtime.getSnapshot().capabilities.vector_search, true);
  runtime.stop();
});

test("availability failure enters usable demo mode", async () => {
  const down = provider({
    health: () =>
      Promise.reject(new runtimeApi.ProviderError("timeout", "slow")),
  });
  const runtime = runtimeApi.createRuntime({
    liveProvider: down,
    demoProvider: provider({
      health: () =>
        Promise.resolve({
          ready: true,
          backend: "bundled",
          retrieval: "demo-lexical",
          vector_search: false,
          document_count: 3,
        }),
    }),
    retryDelays: [],
  });
  await runtime.start();
  assert.equal(runtime.getSnapshot().phase, "demo");
  assert.equal(runtime.getSnapshot().provider, "demo");
  runtime.stop();
});

test("a live search outage retries the same request through demo", async () => {
  const runtime = runtimeApi.createRuntime({
    liveProvider: provider({
      search: () =>
        Promise.reject(new runtimeApi.ProviderError("unavailable", "down", 503)),
    }),
    demoProvider: provider({
      search: (request) =>
        Promise.resolve({ query: request.q, mode: "demo-lexical", hits: [] }),
    }),
    retryDelays: [],
  });
  await runtime.start();
  const result = await runtime.search({ q: "dma" });
  assert.equal(result.mode, "demo-lexical");
  assert.equal(runtime.getSnapshot().provider, "demo");
  runtime.stop();
});

test("a superseded search can never publish after the newer search", async () => {
  const resolvers = [];
  const runtime = runtimeApi.createRuntime({
    liveProvider: provider({
      search: (request) =>
        new Promise((resolve) => resolvers.push({ query: request.q, resolve })),
    }),
    demoProvider: provider(),
    retryDelays: [],
  });
  await runtime.start();
  const oldSearch = runtime.search({ q: "old" });
  const newSearch = runtime.search({ q: "new" });
  resolvers.find((item) => item.query === "new").resolve({ query: "new" });
  assert.equal((await newSearch).query, "new");
  resolvers.find((item) => item.query === "old").resolve({ query: "old" });
  await assert.rejects(oldSearch, { name: "AbortError" });
  runtime.stop();
});

test("recovery advertises live availability but requires explicit switch", async () => {
  var healthy = false;
  const runtime = runtimeApi.createRuntime({
    liveProvider: provider({
      health: () =>
        healthy
          ? provider().health()
          : Promise.reject(new runtimeApi.ProviderError("offline", "offline")),
    }),
    demoProvider: provider(),
    retryDelays: [],
  });
  await runtime.start();
  healthy = true;
  await runtime.retryLive();
  assert.equal(runtime.getSnapshot().provider, "demo");
  assert.equal(runtime.getSnapshot().liveAvailable, true);
  runtime.switchToLive();
  assert.equal(runtime.getSnapshot().provider, "live");
  runtime.stop();
});

test("retrying a changed endpoint from live returns to live state", async () => {
  const runtime = runtimeApi.createRuntime({
    liveProvider: provider(),
    demoProvider: provider(),
    retryDelays: [],
  });
  await runtime.start();
  await runtime.retryLive();
  assert.equal(runtime.getSnapshot().phase, "live");
  assert.equal(runtime.getSnapshot().provider, "live");
  runtime.stop();
});

test("full-text-only capabilities never claim vector retrieval", () => {
  const capabilities = runtimeApi.normalizeCapabilities(
    {
      ready: true,
      backend: "postgres",
      retrieval: "fulltext-only",
      vector_search: false,
      document_count: 9,
    },
    "live"
  );
  assert.equal(capabilities.vector_search, false);
  assert.equal(capabilities.retrieval, "fulltext-only");
  assert.equal(runtimeApi.normalizeCapabilities({}, "live").ready, false);
});

test("invalid live result shapes are availability failures", () => {
  assert.throws(
    () => runtimeApi.validateSearchResponse({ hits: "not-an-array" }),
    (error) => error.code === "invalid-response"
  );
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
node --test test/frontend/search-runtime.test.js
```

Expected: FAIL because `frontend/search-runtime.js` does not exist.

- [ ] **Step 3: Implement the live provider**

Create `frontend/search-runtime.js` as a UMD module. Implement these exact
building blocks first:

```js
function ProviderError(code, message, status) {
  this.name = "ProviderError";
  this.code = code;
  this.message = message;
  this.status = status || 0;
}
ProviderError.prototype = Object.create(Error.prototype);

function abortError() {
  var error = new Error("Request superseded");
  error.name = "AbortError";
  return error;
}

function normalizeCapabilities(body, provider) {
  body = body || {};
  return Object.freeze({
    provider: provider,
    ready:
      body.ready === true ||
      (body.ready == null && body.index_exists === true),
    backend: String(body.backend || (provider === "demo" ? "bundled" : "?")),
    retrieval: String(
      body.retrieval || (provider === "demo" ? "demo-lexical" : "hybrid")
    ),
    vector_search: body.vector_search === true,
    document_count: Math.max(0, Number(body.document_count) || 0),
    label:
      provider === "demo"
        ? "Demo · " + (Number(body.document_count) || 0) + " bundled documents"
        : "Live · " + String(body.backend || "backend"),
  });
}

function validateSearchResponse(body) {
  if (
    !body ||
    !Array.isArray(body.hits) ||
    !Number.isFinite(Number(body.total)) ||
    !body.facets ||
    typeof body.facets !== "object"
  ) {
    throw new ProviderError(
      "invalid-response",
      "Backend returned an invalid search response"
    );
  }
  return body;
}

function validateSummaryResponse(body) {
  if (!body || typeof body.answer !== "string" || !Array.isArray(body.citations)) {
    throw new ProviderError(
      "invalid-response",
      "Backend returned an invalid summary response"
    );
  }
  return body;
}

function validateSourcesResponse(body) {
  if (!body || !Array.isArray(body.sources)) {
    throw new ProviderError(
      "invalid-response",
      "Backend returned an invalid source response"
    );
  }
  return body;
}

function toSearchParams(request) {
  var params = new URLSearchParams();
  params.set("q", request.q || "");
  params.set("mode", request.mode || "hybrid");
  params.set("page", String(request.page || 1));
  params.set("per_page", String(request.per_page || 20));
  ["source", "kind", "category", "language"].forEach(function (key) {
    ((request.filters || {})[key] || []).forEach(function (value) {
      params.append(key, value);
    });
  });
  ["has_code", "has_equations"].forEach(function (key) {
    if ((request.filters || {})[key] === "true") params.set(key, "true");
  });
  return params;
}
```

Implement `createLiveProvider` with:

```js
function createLiveProvider(options) {
  var fetchImpl = options.fetchImpl || fetch;
  var getBaseUrl = options.getBaseUrl;
  var healthTimeoutMs = options.healthTimeoutMs || 4000;
  var requestTimeoutMs = options.requestTimeoutMs || 15000;

  function url(path) {
    return String(getBaseUrl() || "").replace(/\/+$/, "") + "/api/v1" + path;
  }

  function fetchJSON(path, init, deadlineMs, outerSignal) {
    var controller = new AbortController();
    var timedOut = false;
    var forwardAbort = function () {
      controller.abort();
    };
    if (outerSignal) {
      if (outerSignal.aborted) controller.abort();
      else outerSignal.addEventListener("abort", forwardAbort, { once: true });
    }
    var timer = setTimeout(function () {
      timedOut = true;
      controller.abort();
    }, deadlineMs);
    init = Object.assign({}, init || {}, { signal: controller.signal });
    return fetchImpl(url(path), init)
      .then(function (response) {
        return response
          .json()
          .catch(function () {
            throw new ProviderError(
              "invalid-response",
              "Backend returned invalid JSON",
              response.status
            );
          })
          .then(function (body) {
            if (!response.ok) {
              throw new ProviderError(
                response.status >= 500 ? "unavailable" : "client",
                body.error || "HTTP " + response.status,
                response.status
              );
            }
            return body;
          });
      })
      .catch(function (error) {
        if (timedOut) throw new ProviderError("timeout", "Backend timed out");
        if (outerSignal && outerSignal.aborted) throw abortError();
        if (error instanceof ProviderError) throw error;
        throw new ProviderError(
          navigator.onLine === false ? "offline" : "unavailable",
          error.message || "Backend unavailable"
        );
      })
      .finally(function () {
        clearTimeout(timer);
        if (outerSignal) {
          outerSignal.removeEventListener("abort", forwardAbort);
        }
      });
  }

  return {
    health: function (signal) {
      return fetchJSON("/health", {}, healthTimeoutMs, signal).then(function (body) {
        return normalizeCapabilities(body, "live");
      });
    },
    search: function (request, signal) {
      return fetchJSON(
        "/search?" + toSearchParams(request).toString(),
        {},
        requestTimeoutMs,
        signal
      ).then(validateSearchResponse);
    },
    summarize: function (request, signal) {
      return fetchJSON(
        "/summarize",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            q: request.query,
            ids: request.documentIds || [],
          }),
        },
        requestTimeoutMs,
        signal
      ).then(validateSummaryResponse);
    },
    sources: function (signal) {
      return fetchJSON("/sources", {}, requestTimeoutMs, signal).then(
        validateSourcesResponse
      );
    },
  };
}
```

Guard the `navigator` reference with
`typeof navigator !== "undefined"` so Node tests run.

- [ ] **Step 4: Implement the runtime state machine**

In the same module, implement `createRuntime(options)` with:

```js
function createRuntime(options) {
  var live = options.liveProvider;
  var demo = options.demoProvider;
  var retryDelays = options.retryDelays || [10000, 30000, 60000];
  var listeners = [];
  var retryIndex = 0;
  var retryTimer = null;
  var stopped = false;
  var liveCapabilities = null;
  var searchGeneration = 0;
  var summaryGeneration = 0;
  var controllers = { health: null, search: null, summary: null, sources: null };
  var snapshot = {
    phase: "connecting",
    provider: "demo",
    capabilities: null,
    liveAvailable: false,
    reason: "",
  };

  function publish(patch) {
    snapshot = Object.freeze(Object.assign({}, snapshot, patch));
    listeners.slice().forEach(function (listener) {
      listener(snapshot);
    });
    return snapshot;
  }

  function controllerFor(key) {
    if (controllers[key]) controllers[key].abort();
    controllers[key] = new AbortController();
    return controllers[key];
  }

  function availabilityError(error) {
    return (
      error &&
      error.name !== "AbortError" &&
      ["timeout", "offline", "unavailable", "invalid-response"].indexOf(
        error.code
      ) >= 0
    );
  }

  function scheduleReconnect() {
    if (stopped || !retryDelays.length || retryTimer) return;
    var delay = retryDelays[Math.min(retryIndex, retryDelays.length - 1)];
    retryIndex += 1;
    retryTimer = setTimeout(function () {
      retryTimer = null;
      if (
        (typeof navigator !== "undefined" && navigator.onLine === false) ||
        (typeof document !== "undefined" && document.hidden)
      ) {
        scheduleReconnect();
        return;
      }
      retryLive().catch(function () {});
    }, delay);
  }

  function enterDemo(reason) {
    return demo.health().then(function (capabilities) {
      publish({
        phase: "demo",
        provider: "demo",
        capabilities: normalizeCapabilities(capabilities, "demo"),
        liveAvailable: false,
        reason: reason || "Backend unavailable",
      });
      scheduleReconnect();
      return snapshot;
    });
  }

  function start() {
    stopped = false;
    publish({ phase: "connecting", reason: "" });
    var controller = controllerFor("health");
    return live
      .health(controller.signal)
      .then(function (capabilities) {
        liveCapabilities = normalizeCapabilities(capabilities, "live");
        if (!liveCapabilities.ready) {
          return enterDemo("Backend is not ready");
        }
        retryIndex = 0;
        return publish({
          phase: "live",
          provider: "live",
          capabilities: liveCapabilities,
          liveAvailable: true,
          reason: "",
        });
      })
      .catch(function (error) {
        if (error.name === "AbortError") throw error;
        return enterDemo(error.code || "unavailable");
      });
  }

  function retryLive() {
    publish({ phase: "reconnecting" });
    var controller = controllerFor("health");
    return live
      .health(controller.signal)
      .then(function (capabilities) {
        liveCapabilities = normalizeCapabilities(capabilities, "live");
        retryIndex = 0;
        if (snapshot.provider === "demo") {
          publish({
            phase: "demo",
            liveAvailable: liveCapabilities.ready,
            reason: liveCapabilities.ready
              ? "Full index available"
              : "Backend is not ready",
          });
        } else {
          publish({
            phase: "live",
            provider: "live",
            capabilities: liveCapabilities,
            liveAvailable: liveCapabilities.ready,
            reason: "",
          });
        }
        return snapshot;
      })
      .catch(function (error) {
        if (error.name === "AbortError") throw error;
        return enterDemo(error.code || "unavailable");
      });
  }

  function switchToLive() {
    if (!liveCapabilities || !liveCapabilities.ready) {
      throw new ProviderError("unavailable", "Live backend is not ready");
    }
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = null;
    publish({
      phase: "live",
      provider: "live",
      capabilities: liveCapabilities,
      liveAvailable: true,
      reason: "",
    });
  }

  function useDemo(reason) {
    return enterDemo(reason || "Demo selected");
  }

  function search(request) {
    searchGeneration += 1;
    summaryGeneration += 1;
    var generation = searchGeneration;
    var controller = controllerFor("search");
    if (controllers.summary) controllers.summary.abort();
    var selected = snapshot.provider === "live" ? live : demo;
    return selected
      .search(request, controller.signal)
      .catch(function (error) {
        if (snapshot.provider !== "live" || !availabilityError(error)) throw error;
        return enterDemo(error.code).then(function () {
          return demo.search(request, controller.signal);
        });
      })
      .then(function (result) {
        if (generation !== searchGeneration) throw abortError();
        return result;
      });
  }

  function summarize(request) {
    summaryGeneration += 1;
    var generation = summaryGeneration;
    var controller = controllerFor("summary");
    var selected = snapshot.provider === "live" ? live : demo;
    return selected.summarize(request, controller.signal).then(function (result) {
      if (generation !== summaryGeneration) throw abortError();
      return result;
    });
  }

  function sources() {
    var controller = controllerFor("sources");
    var selected = snapshot.provider === "live" ? live : demo;
    return selected.sources(controller.signal);
  }

  function stop() {
    stopped = true;
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = null;
    Object.keys(controllers).forEach(function (key) {
      if (controllers[key]) controllers[key].abort();
    });
  }

  return {
    getSnapshot: function () { return snapshot; },
    subscribe: function (listener) {
      listeners.push(listener);
      listener(snapshot);
      return function () {
        listeners = listeners.filter(function (item) { return item !== listener; });
      };
    },
    start: start,
    stop: stop,
    retryLive: retryLive,
    switchToLive: switchToLive,
    useDemo: useDemo,
    search: search,
    summarize: summarize,
    sources: sources,
  };
}
```

Export `ProviderError`, `createLiveProvider`, `createRuntime`,
`normalizeCapabilities`, `validateSearchResponse`, `validateSummaryResponse`,
`validateSourcesResponse`, and `toSearchParams` from the UMD factory.

Add `search-runtime.js` to `STATIC_ASSETS`.

- [ ] **Step 5: Run runtime and build tests**

Run:

```powershell
node --test test/frontend/search-runtime.test.js test/frontend/demo-search.test.js test/frontend/build.test.mjs
npm run build
```

Expected: 16 tests pass; root build reports eight copied assets.

- [ ] **Step 6: Commit Task 3**

```powershell
git add frontend/search-runtime.js frontend/build.mjs test/frontend/search-runtime.test.js
git commit -m "Preserve user work across backend outages and recovery" `
  -m "Route search through cancellable Live and Demo providers with explicit state transitions and stale-response protection." `
  -m "Constraint: Recovery cannot silently replace the current result set" `
  -m "Rejected: Automatic live takeover because it changes evidence beneath the user" `
  -m "Confidence: high" `
  -m "Scope-risk: moderate" `
  -m "Directive: Only availability-class failures may trigger Demo Mode" `
  -m "Tested: live startup, demo fallback, request cancellation, recovery gate, capability normalization"
```

---

### Task 4: Truthful Backend Capability Contract

**Files:**

- Create: `test/engine/test_api_health.py`
- Modify: `allthethings/engine_api/views.py:195-223`

**Interfaces:**

- Consumes: existing `EngineConfig`, backend facade, and optional PostgreSQL
  `has_vector()`.
- Produces backward-compatible `/api/v1/health` additions:
  - `ready: bool`
  - `retrieval: "hybrid" | "fulltext-only" | "unavailable"`
  - `vector_search: bool`

- [ ] **Step 1: Write failing health-contract tests**

Create `test/engine/test_api_health.py`:

```python
"""Truthful /api/v1/health retrieval capability reporting."""

from types import SimpleNamespace

import pytest

pytest.importorskip("flask")

from flask import Flask  # noqa: E402

from allthethings.engine_api import views  # noqa: E402
from allthethings.engine_api.views import engine_api  # noqa: E402
from engine.config import EngineConfig  # noqa: E402


def _client():
    app = Flask(__name__)
    app.register_blueprint(engine_api)
    return app.test_client()


def _base_backend(monkeypatch, backend_name):
    config = EngineConfig(backend=backend_name)
    monkeypatch.setattr(views, "get_config", lambda: config)
    monkeypatch.setattr(views.backend, "index_exists", lambda config: True)
    monkeypatch.setattr(views.backend, "count", lambda config: 12)
    return config


def test_elasticsearch_health_reports_ready_hybrid(monkeypatch):
    _base_backend(monkeypatch, "elasticsearch")
    body = _client().get("/api/v1/health").get_json()
    assert body["ready"] is True
    assert body["retrieval"] == "hybrid"
    assert body["vector_search"] is True
    assert body["document_count"] == 12


def test_postgres_without_vector_reports_fulltext_only(monkeypatch):
    _base_backend(monkeypatch, "postgres")
    monkeypatch.setattr(
        "engine.pg.store.get_store",
        lambda config: SimpleNamespace(has_vector=lambda: False),
    )
    body = _client().get("/api/v1/health").get_json()
    assert body["ready"] is True
    assert body["retrieval"] == "fulltext-only"
    assert body["vector_search"] is False


def test_unavailable_backend_is_not_ready(monkeypatch):
    _base_backend(monkeypatch, "elasticsearch")

    def unavailable(config):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(views.backend, "index_exists", unavailable)
    body = _client().get("/api/v1/health").get_json()
    assert body["ready"] is False
    assert body["retrieval"] == "unavailable"
    assert body["vector_search"] is False
    assert body["index_exists"] is False
```

- [ ] **Step 2: Run the health tests to verify they fail**

Run:

```powershell
py -m pytest test/engine/test_api_health.py -q -c NUL --noconftest
```

Expected: three failures because `ready` is absent and Elasticsearch does not
report `retrieval` or `vector_search`.

- [ ] **Step 3: Extend the health response without removing existing fields**

Replace the capability portion of `health()` with:

```python
    try:
        status["index_exists"] = es_index.index_exists(config)
        status["document_count"] = es_index.count(config)
        status["backend_status"] = "ok"
        has_vector = True
        if config.backend == "postgres":
            from engine.pg.store import get_store

            has_vector = get_store(config).has_vector()
        status["ready"] = bool(status["index_exists"])
        status["retrieval"] = "hybrid" if has_vector else "fulltext-only"
        status["vector_search"] = has_vector
    except Exception as exc:
        status["backend_status"] = f"unavailable: {exc}"
        status["index_exists"] = False
        status["document_count"] = 0
        status["ready"] = False
        status["retrieval"] = "unavailable"
        status["vector_search"] = False
```

Do not change the endpoint status code or remove `backend_status`,
`index_exists`, `document_count`, `backend`, `index`, or `embedding_model`.

- [ ] **Step 4: Run health and complete engine tests**

Run:

```powershell
py -m pytest test/engine/test_api_health.py -q -c NUL --noconftest
py -m pytest test/engine -q -c NUL --noconftest
```

Expected: new health tests pass; existing engine suite remains green.

- [ ] **Step 5: Commit Task 4**

```powershell
git add allthethings/engine_api/views.py test/engine/test_api_health.py
git commit -m "Tell clients which retrieval capability is actually ready" `
  -m "Extend health metadata consistently across Elasticsearch, vector-enabled Postgres, full-text-only Postgres, and unavailable backends." `
  -m "Constraint: Existing health fields and HTTP behavior remain backward compatible" `
  -m "Confidence: high" `
  -m "Scope-risk: narrow" `
  -m "Directive: UI mode availability must derive from this contract" `
  -m "Tested: health capability tests; isolated engine suite"
```

---

### Task 5: Integrate Live/Demo Runtime into the Workbench

**Files:**

- Create: `test/frontend/static-contract.test.js`
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/styles.css`

**Interfaces:**

- Consumes:
  - `window.EngineDemoCorpus`
  - `window.EngineDemoSearch.createProvider(corpus)`
  - `window.EngineSearchRuntime.createLiveProvider(options)`
  - `window.EngineSearchRuntime.createRuntime(options)`
- Produces:
  - truthful Live/Demo/recovery status;
  - immediate demo fallback;
  - runtime-routed search, summaries, and source catalog;
  - accessible mode announcements and explicit recovery controls.

- [ ] **Step 1: Write failing static integration tests**

Create `test/frontend/static-contract.test.js`:

```js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");

const html = fs.readFileSync("frontend/index.html", "utf8");
const app = fs.readFileSync("frontend/app.js", "utf8");
const css = fs.readFileSync("frontend/styles.css", "utf8");

test("runtime scripts load before app.js in dependency order", () => {
  const order = [
    "config.js",
    "demo-corpus.js",
    "demo-search.js",
    "search-runtime.js",
    "app.js",
  ].map((name) => html.indexOf('src="' + name + '"'));
  assert.ok(order.every((position) => position >= 0));
  assert.deepEqual(order, order.slice().sort((a, b) => a - b));
});

test("status UI exposes a badge, recovery action, and polite live region", () => {
  assert.match(html, /id="runtime-badge"/);
  assert.match(html, /id="runtime-action"/);
  assert.match(html, /id="runtime-notice"/);
  assert.match(html, /id="runtime-announcer"[^>]*aria-live="polite"/);
});

test("application routes product operations through SearchRuntime", () => {
  assert.match(app, /EngineSearchRuntime\.createRuntime/);
  assert.match(app, /runtime\.search\(/);
  assert.match(app, /runtime\.summarize\(/);
  assert.match(app, /runtime\.sources\(/);
  assert.match(app, /runtime\.retryLive\(/);
});

test("styles distinguish demo and recovery state without animation", () => {
  assert.match(css, /\.runtime-badge\.is-demo/);
  assert.match(css, /\.runtime-badge\.is-live/);
  assert.match(css, /\.runtime-badge\.is-reconnecting/);
  assert.match(css, /\.sr-only/);
});
```

- [ ] **Step 2: Run the contract tests to verify they fail**

Run:

```powershell
node --test test/frontend/static-contract.test.js
```

Expected: four failures because the runtime is not mounted.

- [ ] **Step 3: Mount runtime scripts and accessible status controls**

In `frontend/index.html`, extend the connection status cell to:

```html
<div class="status-cell status-grow" id="status-conn">
  <span class="led led-gray" id="conn-led"></span>
  <span id="conn-text">Connecting…</span>
  <span class="runtime-badge is-connecting" id="runtime-badge">CONNECTING</span>
  <button class="status-action" id="runtime-action" type="button" hidden>
    Retry Live
  </button>
</div>
```

Add this visually hidden live region before the closing `.app-window`:

```html
<div id="runtime-announcer" class="sr-only" aria-live="polite"></div>
```

Add this visible mode explanation as the first child of `#content-scroll`:

```html
<div id="runtime-notice" class="runtime-notice" role="status" hidden></div>
```

Load scripts in this exact order:

```html
<script src="config.js"></script>
<script src="demo-corpus.js"></script>
<script src="demo-search.js"></script>
<script src="search-runtime.js"></script>
<script src="app.js"></script>
```

- [ ] **Step 4: Instantiate the runtime and define the request adapter**

In `frontend/app.js`, remove `getJSON()`. After the state declarations, add:

```js
  var demoProvider = window.EngineDemoSearch.createProvider(
    window.EngineDemoCorpus
  );
  var liveProvider = window.EngineSearchRuntime.createLiveProvider({
    getBaseUrl: apiBase,
    healthTimeoutMs: 4000,
    requestTimeoutMs: 15000,
  });
  var runtime = window.EngineSearchRuntime.createRuntime({
    liveProvider: liveProvider,
    demoProvider: demoProvider,
    retryDelays: [10000, 30000, 60000],
  });
  var runtimeSnapshot = runtime.getSnapshot();

  function currentRequest() {
    var copiedFilters = {};
    Object.keys(state.filters).forEach(function (key) {
      copiedFilters[key] = Array.isArray(state.filters[key])
        ? state.filters[key].slice()
        : state.filters[key];
    });
    return {
      q: state.q,
      mode: state.mode,
      page: state.page,
      per_page: state.per_page,
      filters: copiedFilters,
    };
  }
```

Keep `buildQuery()` for browser URL synchronization only.

- [ ] **Step 5: Route search and summaries through the runtime**

Change `doSearch()` from `getJSON("/search?...")` to:

```js
    runtime
      .search(currentRequest())
      .then(function (data) {
        if (data.error) return renderError(data.error);
        lastFacets = data.facets || {};
        renderTree();
        renderResults(data);
        renderPager(data);
        setMetrics(
          data.total +
            " result" +
            (data.total === 1 ? "" : "s") +
            " · " +
            (data.took_ms || 0) +
            "ms · " +
            (data.mode || state.mode)
        );
        $("#pane-count").textContent = "(" + data.total + ")";
        loadSummary(data.hits || []);
      })
      .catch(function (error) {
        if (error && error.name === "AbortError") return;
        renderError(error.message || String(error));
      });
```

Change `loadSummary()` to accept the current hits and call:

```js
    runtime
      .summarize({
        query: state.q,
        documentIds: hits.map(function (hit) {
          return hit.document.id;
        }),
      })
```

Keep the existing safe answer/citation rendering. Ignore `AbortError` in the
summary catch; hide the summary for other errors.

Change `loadSourcesCatalog()` to:

```js
  function loadSourcesCatalog() {
    runtime
      .sources()
      .then(function (data) {
        sourcesCatalog = (data && data.sources) || [];
      })
      .catch(function (error) {
        if (!error || error.name !== "AbortError") sourcesCatalog = [];
      });
  }
```

- [ ] **Step 6: Render truthful capabilities and recovery actions**

Add:

```js
  function applyRuntimeSnapshot(next) {
    runtimeSnapshot = next;
    var badge = $("#runtime-badge");
    var action = $("#runtime-action");
    var notice = $("#runtime-notice");
    var hasCapabilities = next.capabilities != null;
    var capabilities = next.capabilities || {};
    var phaseClass =
      next.phase === "reconnecting"
        ? "is-reconnecting"
        : next.provider === "live"
          ? "is-live"
          : next.phase === "connecting"
            ? "is-connecting"
            : "is-demo";
    badge.className = "runtime-badge " + phaseClass;
    badge.textContent =
      next.phase === "reconnecting"
        ? "RECONNECTING"
        : next.provider === "live"
          ? "LIVE"
          : next.phase === "connecting"
            ? "CONNECTING"
            : "DEMO";

    var vectorAvailable = capabilities.vector_search === true;
    var hybridOption = $('#mode option[value="hybrid"]');
    var semanticOption = $('#mode option[value="semantic"]');
    var lexicalOption = $('#mode option[value="bm25"]');
    hybridOption.disabled = !hasCapabilities || !vectorAvailable;
    semanticOption.disabled = !hasCapabilities || !vectorAvailable;
    lexicalOption.textContent =
      next.phase === "demo" ? "Demo lexical" : "Lexical (BM25)";
    if (hasCapabilities && !vectorAvailable && state.mode !== "bm25") {
      state.mode = "bm25";
      $("#mode").value = "bm25";
      syncUrl();
    }

    action.hidden = next.phase !== "demo";
    action.textContent = next.liveAvailable
      ? "Switch to Live"
      : "Retry Live";
    notice.hidden = next.phase !== "demo";
    notice.textContent =
      "Demo Mode searches " +
      (capabilities.document_count || 0) +
      " bundled documents with deterministic lexical matching. " +
      "Use Live Mode for the full index.";
    $("#runtime-announcer").textContent =
      next.phase === "connecting" || next.phase === "reconnecting"
        ? "Checking the full search backend."
        : next.provider === "demo"
        ? "Demo Mode. Searching " +
          (capabilities.document_count || 0) +
          " bundled documents."
        : next.phase === "live"
          ? "Live Mode. Full search backend connected."
          : "Search runtime unavailable.";
    setConn(
      next.phase === "live" || next.phase === "demo",
      capabilities.label || next.reason || "Connecting…"
    );
    $("#engine-text").textContent =
      "Engine: " + (capabilities.retrieval || "checking");
  }
```

Add this handler; register it once during `init()` in Step 7:

```js
  function handleRuntimeAction() {
    if (runtime.getSnapshot().liveAvailable) {
      runtime.switchToLive();
      loadSourcesCatalog();
      if (state.q) doSearch();
    } else {
      runtime.retryLive();
    }
  }
```

Update the no-results Semantic link so it is only rendered when
`(runtimeSnapshot.capabilities || {}).vector_search === true`; otherwise advise
clearing filters or switching to Live Mode.

- [ ] **Step 7: Update the endpoint dialog and initialization**

In `openApiDialog()`:

- Keep the endpoint input and status row.
- Replace the action buttons with:

```js
      '<div class="dialog-actions">' +
      '<button class="btn btn-default" id="api-ok">Save and Retry Live</button>' +
      '<button class="btn" id="api-demo">Use Demo</button>' +
      '<button class="btn" data-close>Cancel</button></div>'
```

- Register these handlers in the dialog `onMount` callback:

```js
        $("#api-test").addEventListener("click", function () {
          var status = $("#api-status");
          var base = normalizeBase($("#api-input").value);
          var probe = window.EngineSearchRuntime.createLiveProvider({
            getBaseUrl: function () { return base; },
            healthTimeoutMs: 4000,
            requestTimeoutMs: 4000,
          });
          status.textContent = "Testing…";
          probe.health().then(function (health) {
            status.textContent = health.ready
              ? "✓ Connected · " + health.document_count + " docs"
              : "Backend responded but is not ready";
            status.className = health.ready ? "status-ok" : "status-err";
          }).catch(function (error) {
            status.textContent = "✕ " + (error.message || "Unreachable");
            status.className = "status-err";
          });
        });
        $("#api-ok").addEventListener("click", function () {
          localStorage.setItem(
            "engine_api_base",
            normalizeBase($("#api-input").value)
          );
          closeDialog();
          runtime.retryLive();
        });
        $("#api-demo").addEventListener("click", function () {
          closeDialog();
          runtime.useDemo("Demo selected").then(function () {
            loadSourcesCatalog();
            doSearch();
          });
        });
```

Initialize in this order:

```js
    readState();
    renderTree();
    renderWelcome();
    runtime.subscribe(applyRuntimeSnapshot);
    $("#runtime-action").addEventListener("click", handleRuntimeAction);
    runtime
      .start()
      .then(function () {
        loadSourcesCatalog();
        doSearch();
      })
      .catch(function (error) {
        if (!error || error.name !== "AbortError") renderError(error.message);
      });
    window.addEventListener("beforeunload", runtime.stop);
```

Remove the old `refreshHealth()` call and implementation. Preserve all existing
menu, dialog, query, result, filter, and keyboard initialization.

- [ ] **Step 8: Add state styles without motion**

Append to `frontend/styles.css`:

```css
.runtime-badge {
  display: inline-block;
  margin-left: 6px;
  padding: 1px 5px;
  border: 1px solid var(--win-shadow);
  background: var(--win-highlight);
  font-size: 10px;
  font-weight: 700;
  line-height: 1.2;
}
.runtime-badge.is-live { color: #006000; background: #c8f0c8; }
.runtime-badge.is-demo { color: #704000; background: #fff1b8; }
.runtime-badge.is-reconnecting,
.runtime-badge.is-connecting { color: #404040; background: #e8e8e8; }
.status-action {
  margin-left: 6px;
  border: 0;
  background: transparent;
  color: #000080;
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
}
.runtime-notice {
  margin: 6px;
  padding: 7px 9px;
  border: 1px solid #9a761d;
  background: #fff1b8;
  color: #4d3500;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
```

Do not add transitions or animations.

- [ ] **Step 9: Run all frontend tests and builds**

Run:

```powershell
node --test test/frontend/static-contract.test.js test/frontend/search-runtime.test.js test/frontend/demo-search.test.js test/frontend/build.test.mjs
npm run build
npm --prefix frontend run build
```

Expected: all Node tests pass; both builds report eight copied assets.

- [ ] **Step 10: Commit Task 5**

```powershell
git add frontend/index.html frontend/app.js frontend/styles.css test/frontend/static-contract.test.js
git commit -m "Make backend outages a usable and truthful product state" `
  -m "Integrate Live and Demo providers into the existing workbench with capability-aware modes, accessible status, and explicit recovery." `
  -m "Constraint: Preserve query state and the retro workbench identity" `
  -m "Rejected: Silent automatic return to Live because it replaces the evidence currently shown" `
  -m "Confidence: high" `
  -m "Scope-risk: moderate" `
  -m "Directive: Keep network/runtime state out of result rendering helpers" `
  -m "Tested: static integration contracts; runtime and demo tests; root and frontend builds"
```

---

### Task 6: Fast Cross-Platform CI and Operator Documentation

**Files:**

- Create: `test/engine/pytest-smoke.ini`
- Modify: `.github/workflows/ci.yml`
- Modify: `package.json`
- Modify: `frontend/package.json`
- Modify: `README.md`
- Modify: `frontend/README.md`

**Interfaces:**

- Consumes: all tests and builds from Tasks 1–5.
- Produces:
  - `npm run test:frontend`
  - `npm --prefix frontend test`
  - an Ubuntu/Windows `product-smoke` GitHub Actions job.

- [ ] **Step 1: Add OS-independent smoke-test configuration**

Create `test/engine/pytest-smoke.ini`:

```ini
[pytest]
testpaths = test/engine
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -ra --tb=short
filterwarnings =
    error
    ignore::DeprecationWarning
    ignore::PendingDeprecationWarning
```

Verify it avoids the Windows `NUL` cache warning:

```powershell
py -m pytest -q -c test/engine/pytest-smoke.ini --noconftest
```

Expected: the isolated engine suite passes without a `PytestCacheWarning` for
`\\.\NUL`.

- [ ] **Step 2: Add complete package test entrypoints**

Set the root package scripts to:

```json
{
  "scripts": {
    "build": "node frontend/build.mjs dist",
    "test:frontend": "node --test test/frontend/build.test.mjs test/frontend/demo-search.test.js test/frontend/search-runtime.test.js test/frontend/static-contract.test.js"
  }
}
```

Set the frontend package scripts to:

```json
{
  "scripts": {
    "build": "node build.mjs build",
    "test": "node --test ../test/frontend/build.test.mjs ../test/frontend/demo-search.test.js ../test/frontend/search-runtime.test.js ../test/frontend/static-contract.test.js"
  }
}
```

Keep all non-script package metadata unchanged.

- [ ] **Step 3: Add the fast CI lane**

Keep the existing Docker `test` job. Update its checkout action to
`actions/checkout@v4`. Add:

```yaml
  product-smoke:
    name: Product smoke (${{ matrix.os }})
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest]

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.10"
          cache: pip

      - uses: actions/setup-node@v4
        with:
          node-version: "20"

      - name: Install smoke-test dependencies
        run: >-
          python -m pip install
          Flask==2.2.2 Werkzeug==2.2.2 pytest==7.1.3
          SQLAlchemy==1.4.41 httpx==0.23.0

      - name: Test the isolated engine
        run: python -m pytest -q -c test/engine/pytest-smoke.ini --noconftest

      - name: Test the frontend runtime
        run: npm run test:frontend

      - name: Build from the repository root
        run: npm run build

      - name: Build from the frontend directory
        run: npm --prefix frontend run build
```

Do not remove or weaken the existing Docker lane.

- [ ] **Step 4: Document truthful runtime behavior**

In `README.md`, add a short `Always-available frontend` subsection after the
Quickstart:

```markdown
### Always-available frontend

The static workbench prefers the configured `/api/v1` backend. If that backend
is cold or unavailable, it enters a clearly labeled Demo Mode backed by the
same three offline sample records used by `flask engine demo`. Demo retrieval
is deterministic lexical matching—not BM25 or vector search—and cited results
remain available while the app checks for recovery.

```bash
npm run test:frontend
npm run build
```

Both commands are dependency-free and supported on Windows and Linux.
```

In `frontend/README.md`, add:

- the four runtime states;
- the difference between bundled Demo and full-index Live behavior;
- Retry Live, Switch to Live, and Use Demo behavior;
- `npm test` and `npm run build` commands;
- the statement that recovery preserves the query and requires an explicit
  switch.

Do not describe Demo Mode as offline full-corpus search.

- [ ] **Step 5: Run complete automated verification**

Run:

```powershell
py -m pytest -q -c test/engine/pytest-smoke.ini --noconftest
npm run test:frontend
npm run build
npm --prefix frontend run build
git diff --check
```

Expected:

- isolated engine suite passes;
- all frontend tests pass;
- both builds copy eight assets;
- `git diff --check` exits zero.

- [ ] **Step 6: Perform browser verification**

Serve the root build:

```powershell
py -m http.server 4173 --directory dist
```

Verify in a browser:

1. With no backend on `localhost:5000`, the page becomes Demo Mode after the
   health deadline.
2. `DMA circular buffer` returns the ESP32 record and a cited extractive answer.
3. The status bar says `DEMO`, reports three bundled documents, and offers
   Retry Live.
4. Rapid searches never allow an older result to replace the latest query.
5. Source/kind filters and paging remain URL-addressable.
6. The endpoint dialog can remain in Demo, test/save a URL, and retry Live.
7. Simulated recovery exposes Switch to Live without replacing demo results.
8. Unsupported Hybrid and Semantic options are disabled in Demo Mode.
9. Mobile layout at 375×812 has no horizontal overflow.
10. Connection-mode announcements are present in the polite live region.

Record any environment limitation honestly; do not claim a real Live recovery
test when no reachable backend is available.

- [ ] **Step 7: Commit Task 6**

```powershell
git add .github/workflows/ci.yml test/engine/pytest-smoke.ini package.json frontend/package.json README.md frontend/README.md
git commit -m "Keep product boundaries green before deployment" `
  -m "Add fast Windows and Linux smoke coverage for the isolated engine, frontend runtime, and both documented static builds." `
  -m "Constraint: Preserve the existing Docker integration lane" `
  -m "Confidence: high" `
  -m "Scope-risk: narrow" `
  -m "Directive: Demo documentation must never imply full-corpus offline retrieval" `
  -m "Tested: isolated engine suite; frontend test entrypoints; root and frontend builds; browser fallback QA"
```

---

### Task 7: Final Review and Publish-Readiness Verification

**Files:**

- Review all files changed by Tasks 1–6.
- Modify only files required to resolve review findings.

**Interfaces:**

- Consumes: completed implementation.
- Produces: verified, review-ready branch with no unstaged changes.

- [ ] **Step 1: Review the implementation against the approved spec**

Check each acceptance criterion in
`docs/superpowers/specs/2026-07-23-always-available-runtime-design.md`.
Specifically inspect:

- demo labeling and refusal behavior;
- cancellation and generation guards;
- reconnect timer cleanup;
- request error classification;
- health backward compatibility;
- output-directory safety;
- Windows path handling;
- static asset manifest completeness;
- no dependency or lockfile changes.

- [ ] **Step 2: Run the final verification matrix**

```powershell
py -m pytest -q -c test/engine/pytest-smoke.ini --noconftest
npm run test:frontend
npm run build
npm --prefix frontend run build
git diff --check
git status -sb
```

Expected: all tests/builds pass; diff check exits zero; only intentional source
changes are present.

- [ ] **Step 3: Run an independent code review**

Request a reviewer to inspect:

- concurrency and stale-response correctness;
- fallback trust boundaries;
- XSS safety in demo snippets and summaries;
- capability truthfulness;
- timer/controller cleanup;
- build-directory deletion safety;
- accessibility and mobile regressions.

Resolve all Critical and Important findings, then rerun Step 2.

- [ ] **Step 4: Commit review fixes when necessary**

If review requires changes:

```powershell
git add frontend/build.mjs frontend/demo-corpus.js frontend/demo-search.js frontend/search-runtime.js frontend/index.html frontend/app.js frontend/styles.css package.json frontend/package.json allthethings/engine_api/views.py .github/workflows/ci.yml README.md frontend/README.md test/frontend/build.test.mjs test/frontend/demo-search.test.js test/frontend/search-runtime.test.js test/frontend/static-contract.test.js test/engine/test_api_health.py test/engine/pytest-smoke.ini
git commit -m "Close reliability gaps found during final review" `
  -m "Address reviewer findings without broadening the approved always-available runtime scope." `
  -m "Confidence: high" `
  -m "Scope-risk: narrow" `
  -m "Tested: complete final verification matrix"
```

If no files change, do not create an empty commit.

- [ ] **Step 5: Prepare the handoff**

Report:

- branch and commit list;
- exact files changed;
- Live/Demo runtime behavior;
- test and build results;
- browser scenarios tested;
- known environment limitations;
- confirmation that no dependencies were added.
