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
