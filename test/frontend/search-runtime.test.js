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

test("a stale live outage cannot switch a newer search into demo mode", async () => {
  const requests = [];
  const runtime = runtimeApi.createRuntime({
    liveProvider: provider({
      search: (request) =>
        new Promise((resolve, reject) =>
          requests.push({ query: request.q, resolve, reject })
        ),
    }),
    demoProvider: provider(),
    retryDelays: [],
  });
  await runtime.start();
  const oldSearch = runtime.search({ q: "old" });
  const newSearch = runtime.search({ q: "new" });
  requests.find((item) => item.query === "new").resolve({ query: "new" });
  await newSearch;
  requests
    .find((item) => item.query === "old")
    .reject(new runtimeApi.ProviderError("unavailable", "stale outage", 503));
  await assert.rejects(oldSearch, { name: "AbortError" });
  assert.equal(runtime.getSnapshot().provider, "live");
  runtime.stop();
});

test("a stale fallback cannot publish demo after delayed demo health", async () => {
  var resolveDemoHealth;
  const runtime = runtimeApi.createRuntime({
    liveProvider: provider({
      search: (request) =>
        request.q === "old"
          ? Promise.reject(
              new runtimeApi.ProviderError("unavailable", "old outage", 503)
            )
          : Promise.resolve({ query: request.q }),
    }),
    demoProvider: provider({
      health: () =>
        new Promise((resolve) => {
          resolveDemoHealth = resolve;
        }),
    }),
    retryDelays: [],
  });
  await runtime.start();
  const oldSearch = runtime.search({ q: "old" });
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(typeof resolveDemoHealth, "function");
  assert.equal((await runtime.search({ q: "new" })).query, "new");
  resolveDemoHealth(await provider().health());
  await assert.rejects(oldSearch, { name: "AbortError" });
  assert.equal(runtime.getSnapshot().provider, "live");
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

test("client errors remain visible instead of entering demo mode", async () => {
  var demoHealthCalls = 0;
  const runtime = runtimeApi.createRuntime({
    liveProvider: provider({
      health: () =>
        Promise.reject(new runtimeApi.ProviderError("client", "bad route", 404)),
    }),
    demoProvider: provider({
      health: () => {
        demoHealthCalls += 1;
        return provider().health();
      },
    }),
    retryDelays: [],
  });
  await assert.rejects(runtime.start(), { code: "client", status: 404 });
  assert.equal(demoHealthCalls, 0);
  assert.equal(runtime.getSnapshot().phase, "connecting");
  runtime.stop();
});

test("stop rejects a search even when its provider ignores abort", async () => {
  var resolveSearch;
  const runtime = runtimeApi.createRuntime({
    liveProvider: provider({
      search: () =>
        new Promise((resolve) => {
          resolveSearch = resolve;
        }),
    }),
    demoProvider: provider(),
    retryDelays: [],
  });
  await runtime.start();
  const pending = runtime.search({ q: "late" });
  runtime.stop();
  resolveSearch({ query: "late" });
  await assert.rejects(pending, { name: "AbortError" });
});

test("stop prevents pending health from publishing live state", async () => {
  var resolveHealth;
  const runtime = runtimeApi.createRuntime({
    liveProvider: provider({
      health: () =>
        new Promise((resolve) => {
          resolveHealth = resolve;
        }),
    }),
    demoProvider: provider(),
    retryDelays: [],
  });
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

test("settled controllers are not aborted by later requests", async () => {
  const signals = [];
  const runtime = runtimeApi.createRuntime({
    liveProvider: provider({
      search: (request, signal) => {
        signals.push(signal);
        return Promise.resolve({ query: request.q });
      },
    }),
    demoProvider: provider(),
    retryDelays: [],
  });
  await runtime.start();
  await runtime.search({ q: "first" });
  assert.equal(signals[0].aborted, false);
  await runtime.search({ q: "second" });
  assert.equal(signals[0].aborted, false);
  runtime.stop();
});

test("successful manual recovery clears the scheduled reconnect", async () => {
  var healthCalls = 0;
  const runtime = runtimeApi.createRuntime({
    liveProvider: provider({
      health: () => {
        healthCalls += 1;
        return healthCalls === 1
          ? Promise.reject(new runtimeApi.ProviderError("offline", "offline"))
          : provider().health();
      },
    }),
    demoProvider: provider(),
    retryDelays: [20],
  });
  await runtime.start();
  await runtime.retryLive();
  await new Promise((resolve) => setTimeout(resolve, 50));
  assert.equal(healthCalls, 2);
  runtime.stop();
});

test("capability labels use an encoding-safe middle dot", () => {
  assert.equal(
    runtimeApi.normalizeCapabilities({ document_count: 3 }, "demo").label,
    "Demo \u00b7 3 bundled documents"
  );
  assert.equal(
    runtimeApi.normalizeCapabilities({ backend: "postgres" }, "live").label,
    "Live \u00b7 postgres"
  );
});
