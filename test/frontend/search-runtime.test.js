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

function demoProvider(overrides) {
  return provider(
    Object.assign(
      {
        health: () =>
          Promise.resolve({
            ready: true,
            backend: "bundled",
            retrieval: "demo-lexical",
            vector_search: false,
            document_count: 3,
          }),
        search: () => {
          throw new Error("Demo must not answer a Live request");
        },
      },
      overrides || {}
    )
  );
}

const down = (code) => () =>
  Promise.reject(new runtimeApi.ProviderError(code || "unavailable", "down", 503));

// Fast timings: every test finishes in milliseconds.
function fastRuntime(live, demo, extra) {
  return runtimeApi.createRuntime(
    Object.assign(
      {
        liveProvider: live,
        demoProvider: demo || demoProvider(),
        retryDelays: [],
        wakeBudgetMs: 60,
        wakeRetryMs: 5,
        slowAfterMs: 5,
      },
      extra || {}
    )
  );
}

const tick = (ms) => new Promise((resolve) => setTimeout(resolve, ms || 0));

test("healthy startup enters live mode with normalized capabilities", async () => {
  const runtime = fastRuntime(provider());
  assert.equal(runtime.getSnapshot().phase, "connecting");
  assert.equal(runtime.getSnapshot().provider, "live");
  await runtime.start();
  assert.equal(runtime.getSnapshot().phase, "live");
  assert.equal(runtime.getSnapshot().capabilities.vector_search, true);
  assert.equal(runtime.getSnapshot().lastProbe.ok, true);
  runtime.stop();
});

test("a slow health probe is reported as waking, then goes live — never Demo", async () => {
  var release;
  var demoHealth = 0;
  const phases = [];
  const runtime = fastRuntime(
    provider({
      health: () => new Promise((resolve) => { release = resolve; }),
    }),
    demoProvider({ health: () => { demoHealth += 1; return demoProvider().health(); } })
  );
  runtime.subscribe((s) => phases.push(s.phase));
  const started = runtime.start();
  await tick(20);
  assert.equal(runtime.getSnapshot().phase, "waking");
  release(await provider().health());
  await started;
  assert.equal(runtime.getSnapshot().phase, "live");
  assert.equal(demoHealth, 0);
  assert.ok(phases.indexOf("demo") < 0, phases.join(","));
  runtime.stop();
});

test("an unreachable backend is retried within the wake budget, then reported unavailable", async () => {
  var calls = 0;
  var demoHealth = 0;
  const runtime = fastRuntime(
    provider({ health: () => { calls += 1; return down("timeout")(); } }),
    demoProvider({ health: () => { demoHealth += 1; return demoProvider().health(); } })
  );
  const snapshot = await runtime.start();
  assert.ok(calls > 1, "should retry while waking, got " + calls);
  assert.equal(snapshot.phase, "unavailable");
  assert.equal(snapshot.provider, "live");
  assert.equal(snapshot.liveAvailable, false);
  assert.match(snapshot.reason, /did not answer in time/);
  assert.equal(snapshot.lastProbe.code, "timeout");
  assert.equal(demoHealth, 0, "Demo must not be entered automatically");
  runtime.stop();
});

test("a misconfigured endpoint (HTTP 404) is unavailable at once, with the reason", async () => {
  var calls = 0;
  const runtime = fastRuntime(
    provider({
      health: () => {
        calls += 1;
        return Promise.reject(new runtimeApi.ProviderError("http-client", "bad route", 404));
      },
    })
  );
  const snapshot = await runtime.start();
  assert.equal(calls, 1, "a client error is not a cold start; no wake retries");
  assert.equal(snapshot.phase, "unavailable");
  assert.match(snapshot.reason, /refused the request \(HTTP 404: bad route\)/);
  runtime.stop();
});

test("a backend whose index is not ready keeps waking, then says why", async () => {
  const runtime = fastRuntime(
    provider({ health: () => Promise.resolve({ ready: false, backend: "postgres" }) })
  );
  const snapshot = await runtime.start();
  assert.equal(snapshot.phase, "unavailable");
  assert.equal(snapshot.reason, "Anna's research index is not ready yet");
  runtime.stop();
});

test("a search made while Anna is waking is queued and runs on Live", async () => {
  var release;
  const searched = [];
  const runtime = fastRuntime(
    provider({
      health: () => new Promise((resolve) => { release = resolve; }),
      search: (request) => { searched.push(request.q); return Promise.resolve({ query: request.q, hits: [] }); },
    })
  );
  runtime.start();
  const pending = runtime.search({ q: "dma" });
  await tick(10);
  assert.deepEqual(searched, [], "nothing runs before Live is ready");
  release(await provider().health());
  const result = await pending;
  assert.equal(result.query, "dma");
  assert.deepEqual(searched, ["dma"]);
  runtime.stop();
});

test("a queued search fails with 'unavailable' when Anna never answers — no Demo answer", async () => {
  const runtime = fastRuntime(provider({ health: down() }));
  runtime.start();
  await assert.rejects(runtime.search({ q: "dma" }), { code: "unavailable" });
  assert.equal(runtime.getSnapshot().provider, "live");
  runtime.stop();
});

test("searching while unavailable asks for Anna again", async () => {
  var healthy = false;
  const runtime = fastRuntime(
    provider({ health: () => (healthy ? provider().health() : down()()) })
  );
  await runtime.start();
  assert.equal(runtime.getSnapshot().phase, "unavailable");
  healthy = true;
  const result = await runtime.search({ q: "dma" });
  assert.equal(result.query, "dma");
  assert.equal(runtime.getSnapshot().phase, "live");
  runtime.stop();
});

test("a live search outage is reported, rechecks Live, and never retries through Demo", async () => {
  var healthCalls = 0;
  const runtime = fastRuntime(
    provider({
      health: () => { healthCalls += 1; return provider().health(); },
      search: down("unavailable"),
    })
  );
  await runtime.start();
  await assert.rejects(runtime.search({ q: "dma" }), { code: "unavailable" });
  await tick(10);
  assert.ok(healthCalls >= 2, "the outage triggers a health recheck");
  assert.equal(runtime.getSnapshot().provider, "live");
  runtime.stop();
});

test("a background reconnect restores Live after an outage", async () => {
  var healthy = false;
  const runtime = fastRuntime(
    provider({ health: () => (healthy ? provider().health() : down()()) }),
    null,
    { retryDelays: [5] }
  );
  await runtime.start();
  assert.equal(runtime.getSnapshot().phase, "unavailable");
  assert.ok(runtime.getSnapshot().nextRetryAt > 0);
  healthy = true;
  await tick(40);
  assert.equal(runtime.getSnapshot().phase, "live");
  runtime.stop();
});

test("Demo is entered only on request, and stays selected after Live recovers", async () => {
  var healthy = false;
  const runtime = fastRuntime(
    provider({ health: () => (healthy ? provider().health() : down()()) }),
    demoProvider({ search: (r) => Promise.resolve({ query: r.q, mode: "demo-lexical", hits: [] }) }),
    { retryDelays: [5] }
  );
  await runtime.start();
  assert.equal(runtime.getSnapshot().phase, "unavailable");
  await runtime.useDemo();
  assert.equal(runtime.getSnapshot().phase, "demo");
  assert.equal(runtime.getSnapshot().capabilities.provider, "demo");
  assert.equal((await runtime.search({ q: "dma" })).mode, "demo-lexical");
  healthy = true;
  await tick(40);
  assert.equal(runtime.getSnapshot().provider, "demo");
  assert.equal(runtime.getSnapshot().liveAvailable, true, "Live is offered, not imposed");
  await runtime.useLive();
  assert.equal(runtime.getSnapshot().phase, "live");
  assert.equal(runtime.getSnapshot().provider, "live");
  runtime.stop();
});

test("switching to Demo releases a queued Live search instead of answering it from Demo", async () => {
  const runtime = fastRuntime(
    provider({ health: () => new Promise(() => {}) }),
    demoProvider({ search: () => Promise.resolve({ hits: [] }) })
  );
  runtime.start();
  const pending = runtime.search({ q: "dma" });
  await runtime.useDemo();
  await assert.rejects(pending, { name: "AbortError" });
  runtime.stop();
});

test("useLive({fresh}) re-probes a changed endpoint instead of trusting the old one", async () => {
  var healthCalls = 0;
  const runtime = fastRuntime(
    provider({ health: () => { healthCalls += 1; return provider().health(); } })
  );
  await runtime.start();
  await runtime.useLive();
  assert.equal(healthCalls, 1, "already live: no probe needed");
  await runtime.useLive({ fresh: true });
  assert.equal(healthCalls, 2);
  assert.equal(runtime.getSnapshot().phase, "live");
  runtime.stop();
});

test("switchToLive refuses when Live is not known to be ready", async () => {
  const runtime = fastRuntime(provider({ health: down() }));
  await runtime.start();
  assert.throws(() => runtime.switchToLive(), { code: "unavailable" });
  runtime.stop();
});

test("a superseded search can never publish after the newer search", async () => {
  var resolveFirst;
  const runtime = fastRuntime(
    provider({
      search: (request) =>
        request.q === "first"
          ? new Promise((resolve) => { resolveFirst = resolve; })
          : Promise.resolve({ query: request.q, hits: [] }),
    })
  );
  await runtime.start();
  const first = runtime.search({ q: "first" });
  await tick(0);
  const second = await runtime.search({ q: "second" });
  resolveFirst({ query: "first", hits: [] });
  await assert.rejects(first, { name: "AbortError" });
  assert.equal(second.query, "second");
  runtime.stop();
});

test("a search response teaches the runtime what an older backend's vectors are", async () => {
  const runtime = fastRuntime(
    provider({
      search: () => Promise.resolve({ hits: [], retrieval: { embedding: "hashing" } }),
    })
  );
  await runtime.start();
  assert.equal(runtime.getSnapshot().capabilities.embedding, "unknown");
  await runtime.search({ q: "dma" });
  assert.equal(runtime.getSnapshot().capabilities.embedding, "hashing");
  assert.equal(runtime.getSnapshot().capabilities.semantic_search, false);
  runtime.stop();
});

test("health that reports its vectors is believed", () => {
  const hashing = runtimeApi.normalizeCapabilities(
    { ready: true, vector_search: true, embedding: "hashing", semantic_search: false },
    "live"
  );
  assert.equal(hashing.semantic_search, false);
  const model = runtimeApi.normalizeCapabilities(
    { ready: true, vector_search: true, embedding: "sentence-transformer", semantic_search: true },
    "live"
  );
  assert.equal(model.semantic_search, true);
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
  assert.equal(capabilities.semantic_search, false);
  assert.equal(capabilities.retrieval, "fulltext-only");
  assert.equal(runtimeApi.normalizeCapabilities({}, "live").ready, false);
});

test("invalid live result shapes are availability failures", () => {
  assert.throws(
    () => runtimeApi.validateSearchResponse({ hits: "not-an-array" }),
    (error) => error.code === "invalid-response"
  );
  assert.ok(runtimeApi.availabilityError(new runtimeApi.ProviderError("invalid-response", "x")));
  assert.ok(!runtimeApi.availabilityError(new runtimeApi.ProviderError("http-client", "x", 404)));
});

test("stop rejects a search even when its provider ignores abort", async () => {
  var resolveSearch;
  const runtime = fastRuntime(
    provider({
      search: () => new Promise((resolve) => { resolveSearch = resolve; }),
    })
  );
  await runtime.start();
  const pending = runtime.search({ q: "late" });
  await tick(0);
  runtime.stop();
  resolveSearch({ query: "late" });
  await assert.rejects(pending, { name: "AbortError" });
});

test("stop releases operations still queued behind the probe", async () => {
  const runtime = fastRuntime(provider({ health: () => new Promise(() => {}) }));
  const started = runtime.start();
  const pending = runtime.search({ q: "queued" });
  runtime.stop();
  await assert.rejects(pending, { name: "AbortError" });
  await assert.rejects(started, { name: "AbortError" });
});

test("stop prevents pending health from publishing live state", async () => {
  var resolveHealth;
  const runtime = fastRuntime(
    provider({ health: () => new Promise((resolve) => { resolveHealth = resolve; }) })
  );
  const pending = runtime.start();
  const stoppedSnapshot = runtime.getSnapshot();
  runtime.stop();
  resolveHealth(await provider().health());
  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(runtime.getSnapshot(), stoppedSnapshot);
});

test("non-JSON HTTP 4xx responses remain visible client errors", async () => {
  for (const status of [400, 401, 404]) {
    const live = runtimeApi.createLiveProvider({
      getBaseUrl: () => "https://example.test",
      fetchImpl: () =>
        Promise.resolve({
          ok: false,
          status,
          statusText: "Client request rejected",
          json: () => Promise.reject(new SyntaxError("Unexpected token <")),
        }),
    });
    await assert.rejects(
      live.health(),
      (error) =>
        error.code === "http-client" &&
        error.status === status &&
        error.message === "Client request rejected"
    );
  }
});

test("JSON null HTTP 4xx remains a visible client error", async () => {
  const live = runtimeApi.createLiveProvider({
    getBaseUrl: () => "https://example.test",
    fetchImpl: () =>
      Promise.resolve({
        ok: false,
        status: 404,
        statusText: "Not Found",
        json: () => Promise.resolve(null),
      }),
  });
  await assert.rejects(
    live.health(),
    (error) =>
      error.code === "http-client" &&
      error.status === 404 &&
      error.message === "Not Found"
  );
});

test("an empty endpoint is a configuration error, not a network failure", async () => {
  const live = runtimeApi.createLiveProvider({
    getBaseUrl: () => "",
    fetchImpl: () => { throw new Error("must not fetch"); },
  });
  await assert.rejects(live.health(), { code: "not-configured" });
});

test("settled controllers are not aborted by later requests", async () => {
  const signals = [];
  const runtime = fastRuntime(
    provider({
      search: (request, signal) => {
        signals.push(signal);
        return Promise.resolve({ query: request.q });
      },
    })
  );
  await runtime.start();
  await runtime.search({ q: "first" });
  assert.equal(signals[0].aborted, false);
  await runtime.search({ q: "second" });
  assert.equal(signals[0].aborted, false);
  runtime.stop();
});

test("a manual retry joins the probe in flight and clears the scheduled reconnect", async () => {
  var healthCalls = 0;
  const runtime = fastRuntime(
    provider({
      health: () => {
        healthCalls += 1;
        return healthCalls === 1
          ? Promise.reject(new runtimeApi.ProviderError("http-client", "nope", 404))
          : provider().health();
      },
    }),
    null,
    { retryDelays: [30] }
  );
  await runtime.start();
  assert.equal(runtime.getSnapshot().phase, "unavailable");
  await runtime.retryLive();
  assert.equal(runtime.getSnapshot().phase, "live");
  await tick(60);
  assert.equal(healthCalls, 2, "the scheduled reconnect was cancelled");
  runtime.stop();
});

test("capability labels use an encoding-safe middle dot", () => {
  assert.equal(
    runtimeApi.normalizeCapabilities({ document_count: 3 }, "demo").label,
    "Demo · 3 bundled documents"
  );
  assert.equal(
    runtimeApi.normalizeCapabilities({ backend: "postgres" }, "live").label,
    "Live · postgres"
  );
});

test("failure reasons are plain language", () => {
  const P = runtimeApi.ProviderError;
  assert.equal(runtimeApi.describeFailure(new P("timeout", "x")), "Anna's research backend did not answer in time");
  assert.equal(runtimeApi.describeFailure(new P("unavailable", "x")), "Anna's research backend isn't reachable");
  assert.equal(runtimeApi.describeFailure(new P("offline", "x")), "This browser is offline");
});

function verifyReport(extra) {
  return Object.assign({ fingerprint: { matches: true }, excerpts: [], counts: {}, ok: true }, extra || {});
}

test("verification goes to the selected provider and never falls back", async () => {
  const calls = [];
  const runtime = fastRuntime(
    provider({
      verify: () => { calls.push("live"); return Promise.resolve(verifyReport()); },
    }),
    demoProvider({
      verify: () => { calls.push("demo"); return Promise.resolve(verifyReport()); },
    })
  );
  await runtime.start();
  await runtime.verify({ record: {} });
  await runtime.useDemo();
  await runtime.verify({ record: {} });
  assert.deepEqual(calls, ["live", "demo"]);
  runtime.stop();
});

test("a live verification failure is reported rather than retried through demo", async () => {
  var demoCalls = 0;
  const runtime = fastRuntime(
    provider({
      verify: () =>
        Promise.reject(new runtimeApi.ProviderError("http-client", "Not Found", 404)),
    }),
    demoProvider({
      verify: () => { demoCalls += 1; return Promise.resolve(verifyReport()); },
    })
  );
  await runtime.start();
  await assert.rejects(runtime.verify({ record: {} }), { code: "http-client", status: 404 });
  assert.equal(demoCalls, 0);
  assert.equal(runtime.getSnapshot().provider, "live");
  runtime.stop();
});

test("stop rejects a pending verification", async () => {
  var resolveVerify;
  const runtime = fastRuntime(
    provider({
      verify: () => new Promise((resolve) => { resolveVerify = resolve; }),
    })
  );
  await runtime.start();
  const pending = runtime.verify({ record: {} });
  await tick(0);
  runtime.stop();
  resolveVerify(verifyReport());
  await assert.rejects(pending, { name: "AbortError" });
});

test("the live provider posts the packet to the verify endpoint and validates the reply", async () => {
  const seen = [];
  const live = runtimeApi.createLiveProvider({
    getBaseUrl: () => "https://example.test/",
    fetchImpl: (url, init) => {
      seen.push({ url, init });
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(verifyReport()) });
    },
  });
  const report = await live.verify({ record: { schema: "x" }, content_sha256: "a".repeat(64) });
  assert.equal(report.ok, true);
  assert.equal(seen[0].url, "https://example.test/api/v1/evidence/verify");
  assert.equal(seen[0].init.method, "POST");
  assert.equal(JSON.parse(seen[0].init.body).content_sha256, "a".repeat(64));
  assert.throws(
    () => runtimeApi.validateVerifyResponse({ ok: "yes", excerpts: [] }),
    (error) => error.code === "invalid-response"
  );
});

/* ---------------------------------------------- the advertised workflows */

function recordingLive(responses) {
  const seen = [];
  const live = runtimeApi.createLiveProvider({
    getBaseUrl: () => "https://api.example",
    fetchImpl: (url, init) => {
      const path = url.replace("https://api.example/api/v1", "");
      seen.push({ method: (init && init.method) || "GET", path, body: init && init.body ? JSON.parse(init.body) : null });
      const key = Object.keys(responses).find((prefix) => path.startsWith(prefix));
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(responses[key]) });
    },
  });
  return { live, seen };
}

test("document details and related work go to the document endpoints, id-encoded", async () => {
  const { live, seen } = recordingLive({
    "/document/arxiv%3A1%2Fv2/related": { id: "arxiv:1/v2", related: [] },
    "/document/arxiv%3A1%2Fv2": { id: "arxiv:1/v2", title: "T" },
  });
  assert.equal((await live.document("arxiv:1/v2")).title, "T");
  assert.deepEqual((await live.related("arxiv:1/v2")).related, []);
  assert.deepEqual(seen.map((s) => s.method + " " + s.path), [
    "GET /document/arxiv%3A1%2Fv2",
    "GET /document/arxiv%3A1%2Fv2/related?size=6",
  ]);
});

test("compare posts both ids and validates the comparison", async () => {
  const { live, seen } = recordingLive({
    "/compare": { a: {}, b: {}, shared_terms: ["dma"], text_similarity: 0.25 },
  });
  const result = await live.compare("a:1", "b:2");
  assert.equal(result.text_similarity, 0.25);
  assert.deepEqual(seen[0], { method: "POST", path: "/compare", body: { a: "a:1", b: "b:2" } });
  assert.throws(() => runtimeApi.validateCompareResponse({ a: {} }), { code: "invalid-response" });
});

test("collections are scoped to the workspace and fall back for older backends", async () => {
  const { live, seen } = recordingLive({
    "/collections?": { collections: [{ id: 7, name: "RTOS", bookmark_count: 1 }] },
    "/collections/7?": { id: 7, name: "RTOS", bookmarks: [{ document_id: "d" }] },
    "/collections/7/bookmarks": { document_id: "d" },
    "/collections": { id: 8, name: "New" },
  });
  const listed = await live.collections("ws_abc");
  assert.deepEqual(listed.collections[0].bookmarks, [{ document_id: "d" }]);
  await live.createCollection("ws_abc", "New");
  await live.addBookmark("ws_abc", 7, { id: "d", title: "T", url: "https://x", source: "s" });
  await live.removeBookmark("ws_abc", 7, "arxiv:1");
  await live.deleteCollection("ws_abc", 7);
  assert.deepEqual(seen.map((s) => s.method + " " + s.path), [
    "GET /collections?owner=ws_abc&with_bookmarks=1",
    "GET /collections/7?owner=ws_abc",
    "POST /collections",
    "POST /collections/7/bookmarks",
    "DELETE /collections/7/bookmarks/arxiv%3A1?owner=ws_abc",
    "DELETE /collections/7?owner=ws_abc",
  ]);
  assert.deepEqual(seen[2].body, { owner: "ws_abc", name: "New" });
  assert.deepEqual(seen[3].body, { owner: "ws_abc", document_id: "d", title: "T", url: "https://x", source: "s" });
});

test("collections in Demo Mode say they need the backend instead of faking it", async () => {
  const runtime = fastRuntime(provider());
  await runtime.start();
  await runtime.useDemo();
  await assert.rejects(runtime.collections("ws_abc"), { code: "demo-unsupported" });
  await assert.rejects(runtime.addBookmark("ws_abc", 1, { id: "d" }), { code: "demo-unsupported" });
  runtime.stop();
});

test("collection writes do not cancel each other", async () => {
  const resolvers = [];
  const runtime = fastRuntime(
    provider({
      addBookmark: (owner, id, doc, signal) =>
        new Promise((resolve, reject) => {
          resolvers.push(() => resolve({ document_id: doc.id }));
          signal.addEventListener("abort", () => reject(new Error("aborted")));
        }),
    })
  );
  await runtime.start();
  const first = runtime.addBookmark("ws", 1, { id: "a" });
  const second = runtime.addBookmark("ws", 1, { id: "b" });
  await tick(0);
  resolvers.forEach((resolve) => resolve());
  assert.deepEqual((await Promise.all([first, second])).map((b) => b.document_id), ["a", "b"]);
  runtime.stop();
});

test("browse counts never supersede the user's search", async () => {
  var resolveSearch;
  const runtime = fastRuntime(
    provider({
      search: (request) =>
        request.q === "user"
          ? new Promise((resolve) => { resolveSearch = resolve; })
          : Promise.resolve({ query: request.q, hits: [] }),
    })
  );
  await runtime.start();
  const userSearch = runtime.search({ q: "user" });
  await tick(0);
  await runtime.browse({ q: "" });
  resolveSearch({ query: "user", hits: [] });
  assert.equal((await userSearch).query, "user");
  runtime.stop();
});

test("search parameters carry year bounds", () => {
  const params = runtimeApi.toSearchParams({
    q: "dma",
    filters: { year_from: "2019", year_to: "2023", source: ["arxiv"] },
  });
  assert.equal(params.get("year_from"), "2019");
  assert.equal(params.get("year_to"), "2023");
  assert.deepEqual(params.getAll("source"), ["arxiv"]);
  assert.equal(runtimeApi.toSearchParams({ q: "x", filters: { year_from: "abc" } }).get("year_from"), null);
});
