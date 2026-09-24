const assert = require("node:assert/strict");
const test = require("node:test");
const { webcrypto } = require("node:crypto");
const evidence = require("../../frontend/evidence.js");
const demo = require("../../frontend/demo-search.js");
const corpus = require("../../frontend/demo-corpus.js");

const request = { q: "DMA circular buffer", mode: "hybrid", page: 1, per_page: 20, filters: {} };
function record() {
  const results = demo.search(corpus, request);
  const summary = demo.summarize(corpus, request.q, results.hits.map(h => h.document.id));
  return evidence.createRecord(request, results, summary, "demo");
}

test("demo evidence is an exact source slice with explicit UTF-16 offsets", () => {
  const doc = { id: "test:1", title: "DMA", source: "test", abstract: "An unrelated introduction.",
    body: "😀 Intro.\n  DMA transfers samples into circular buffers." };
  const summary = evidence.summarize("DMA circular buffers", [doc]);
  assert.equal(summary.grounding, "source-extract");
  for (const c of summary.citations) for (const e of c.excerpts) {
    assert.equal(e.field, "body");
    assert.equal(doc[e.field].slice(e.start, e.end), e.quote);
    assert.equal(e.offset_unit, "utf-16");
  }
});

test("common question words cannot turn irrelevant sources into evidence", () => {
  const doc = { id: "x", abstract: "The controller is the component that transfers data." };
  assert.equal(evidence.summarize("What is the quantum behavior?", [doc]).grounding, "insufficient-evidence");
});

test("repeated source IDs and repeated passages never multiply citations", () => {
  const doc = { id: "x", abstract: "DMA transfers samples into circular buffers." };
  const result = evidence.summarize("DMA", [doc, doc]);
  assert.equal(result.citations.length, 1);
  assert.equal(result.citations[0].excerpts.length, 1);
});

test("original source references cannot impersonate Anna citation markers", () => {
  const result = evidence.summarize("DMA", [{ id: "x", abstract: "DMA transfers are discussed in reference [99]." }]);
  assert.doesNotMatch(result.answer, /\[99\]/);
  assert.match(result.citations[0].excerpts[0].quote, /\[99\]/);
});

test("a result reports demo lexical provenance without calling its score RRF", () => {
  const result = demo.search(corpus, request);
  assert.deepEqual(result.retrieval.executed, ["demo-lexical"]);
  assert.equal(result.retrieval.fusion, "weighted-lexical");
  assert.equal(result.hits[0].explanation.method, "weighted-lexical");
  assert.ok(result.hits[0].explanation.matched_terms.includes("dma"));
});

test("unsafe protocols and credential-bearing links are not clickable", () => {
  for (const url of ["javascript:alert(1)", "data:text/html,bad", "file:///etc/passwd", "https://user:pass@example.com", "//example.com"]) {
    assert.equal(evidence.safeUrl(url), "");
  }
  assert.equal(evidence.safeUrl("https://example.com/paper"), "https://example.com/paper");
});

test("record snapshots preserve the query and never include API configuration", () => {
  const result = demo.search(corpus, request);
  const input = { ...request, api: "https://secret.example/key", filters: { source: ["espressif"] } };
  const saved = evidence.createRecord(input, result, null, "demo");
  input.filters.source.push("github");
  result.hits[0].explanation.method = "changed";
  assert.deepEqual(saved.request.filters.source, ["espressif"]);
  assert.equal(saved.hits[0].explanation.method, "weighted-lexical");
  assert.doesNotMatch(JSON.stringify(saved), /secret.example/);
});

test("SHA-256 fingerprints are stable for identical content and change with evidence", async () => {
  const a = record(), b = record();
  const first = await evidence.packet(a, webcrypto);
  const second = await evidence.packet(b, webcrypto);
  assert.match(first.content_sha256, /^[a-f0-9]{64}$/);
  assert.equal(first.content_sha256, second.content_sha256);
  b.summary.citations[0].excerpts[0].quote += " changed";
  assert.notEqual(first.content_sha256, await evidence.fingerprint(b, webcrypto));
});

test("canonical object order does not affect fingerprints", async () => {
  assert.equal(await evidence.fingerprint({ z: 1, a: [1, 2] }, webcrypto),
    await evidence.fingerprint({ a: [1, 2], z: 1 }, webcrypto));
});

test("Markdown report contains citations, excerpts, query, and scope", () => {
  const text = evidence.markdown(record());
  assert.match(text, /DMA circular buffer/);
  assert.match(text, /current result page/);
  assert.match(text, /Source report/);
  assert.match(text, /offsets/);
  assert.match(text, /^> /m);
});

test("Markdown cannot turn source HTML or line breaks into active content", () => {
  const saved = record();
  saved.summary.citations[0].excerpts[0].quote = '<script>alert(1)</script>\n# injected heading';
  saved.summary.citations[0].url = "javascript:alert(1)";
  const text = evidence.markdown(saved);
  assert.doesNotMatch(text, /<script>|javascript:/);
  assert.match(text, /> &lt;script&gt;/);
  assert.match(text, /> \\# injected heading/);
});

test("evidence selection supports Unicode query terms", () => {
  const summary = evidence.summarize("résonance", [{ id: "x", abstract: "La résonance est mesurée dans le système expérimental." }]);
  assert.equal(summary.grounding, "source-extract");
});
