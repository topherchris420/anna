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

test("an abbreviation inside a sentence never cuts the excerpt into a fragment", () => {
  // arXiv 1007.2229 was cited as "the respiratory sinus arrhythmia." alone.
  const first = "Using a model of blood pressure dynamics, fluctuations are buffered " +
    "by appropriate heart rate changes: i.e. the respiratory sinus arrhythmia.";
  const doc = { id: "x", abstract: first + " The buffering depends on timing." };
  const [excerpt] = evidence.summarize("respiratory sinus arrhythmia", [doc]).citations[0].excerpts;
  assert.equal(excerpt.quote, first);
  assert.equal(doc.abstract.slice(excerpt.start, excerpt.end), excerpt.quote);
  const text = "Smith et al. compare DMA vs. polling (cf. Fig. 2, Eq. 3), e.g. J. R. Smith. Done.";
  assert.deepEqual(evidence.sentenceSpans(text).map(([s, e]) => text.slice(s, e)),
    ["Smith et al. compare DMA vs. polling (cf. Fig. 2, Eq. 3), e.g. J. R. Smith.", "Done."]);
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

test("records keep a hit's relevance only when the backend reported one", () => {
  const results = demo.search(corpus, request);
  assert.equal("relevance" in evidence.createRecord(request, results, null, "demo").hits[0], false);
  const live = Object.assign({}, results, { hits: [Object.assign({}, results.hits[0], { relevance: 0.91 })] });
  const saved = evidence.createRecord(request, live, null, "live");
  assert.equal(saved.hits[0].relevance, 0.91);
  assert.match(evidence.markdown(saved), /relevance 0\.91/);
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

test("a saved packet verifies against the documents it cites", async () => {
  const saved = await evidence.packet(record(), webcrypto);
  const byId = new Map(corpus.map((d) => [d.id, d]));
  const report = await evidence.verifyRecord(saved, (id) => byId.get(id) || null,
    { cryptoApi: webcrypto, checkedAgainst: { backend: "bundled", index: "demo-corpus" } });
  assert.equal(report.schema, "anna-research-record/v1");
  assert.equal(report.schema_known, true);
  assert.equal(report.fingerprint.matches, true);
  assert.ok(report.excerpts.length > 0);
  assert.ok(report.excerpts.every((e) => e.status === "verified"));
  assert.deepEqual(Object.keys(report.counts), evidence.STATUSES);
  assert.deepEqual(report.checked_against, { backend: "bundled", index: "demo-corpus" });
  assert.equal(report.ok, true);
});

test("tampering after export fails the fingerprint even though the excerpts still hold", async () => {
  const saved = await evidence.packet(record(), webcrypto);
  saved.record.summary.answer = "A claim the sources never made [1]";
  const byId = new Map(corpus.map((d) => [d.id, d]));
  const report = await evidence.verifyRecord(saved, (id) => byId.get(id) || null, { cryptoApi: webcrypto });
  assert.equal(report.fingerprint.matches, false);
  assert.equal(report.excerpts[0].status, "verified");
  assert.equal(report.ok, false);
});

test("excerpt statuses follow the engine contract in both offset units", () => {
  const quote = "DMA transfers samples into circular buffers.";
  const doc = { id: "d", abstract: "The ESP32 DMA engine supports circular buffers.", body: "😀 Intro.\n  " + quote };
  const base = { document_id: "d", field: "body", quote, offset_unit: "utf-16", start: 12, end: 12 + quote.length };
  assert.equal(evidence.verifyExcerpt(base, doc, 1).status, "verified");
  // Engine records count code points: the emoji is one, not two.
  const engine = Object.assign({}, base, { offset_unit: "unicode-code-points", start: 11, end: 11 + quote.length });
  assert.equal(evidence.verifyExcerpt(engine, doc).status, "verified");
  const stale = evidence.verifyExcerpt(Object.assign({}, base, { offset_unit: "unicode-code-points" }), doc);
  assert.equal(stale.status, "relocated");
  assert.equal(stale.found_at, 11);
  assert.equal(evidence.verifyExcerpt(Object.assign({}, base, { quote: "ring buffers" }), doc).status, "drifted");
  assert.equal(evidence.verifyExcerpt(base, null).status, "missing-document");
  assert.equal(evidence.verifyExcerpt(Object.assign({}, base, { field: "title" }), doc).status, "missing-field");
  for (const bad of [{ field: "embedding" }, { start: 1.5 }, { end: 0 }, { start: -1 }, { offset_unit: "bytes" }, { quote: "" }, { document_id: " " }]) {
    const report = evidence.verifyExcerpt(Object.assign({}, base, bad), doc);
    assert.equal(report.status, "invalid-excerpt", JSON.stringify(bad));
    assert.ok(report.detail);
  }
  assert.equal(evidence.verifyExcerpt("not an object", doc).status, "invalid-excerpt");
});

test("sliceField and locate honour the record's offset unit", () => {
  const quote = "DMA transfers samples into circular buffers.";
  const text = "😀 Intro.\n  " + quote;
  assert.equal(evidence.locate(text, quote, "utf-16"), 12);
  assert.equal(evidence.locate(text, quote, "unicode-code-points"), 11);
  assert.equal(evidence.sliceField(text, 12, 12 + quote.length, "utf-16"), quote);
  assert.equal(evidence.sliceField(text, 11, 11 + quote.length, "unicode-code-points"), quote);
  assert.notEqual(evidence.sliceField(text, 11, 11 + quote.length, "utf-16"), quote);
  assert.equal(evidence.locate(text, "ring buffers", "utf-16"), null);
});

test("verifyExcerpts fetches each document once and keeps record order", () => {
  const saved = record();
  const [first] = saved.summary.citations[0].excerpts;
  saved.summary.citations[0].excerpts.push(Object.assign({}, first, { start: first.start + 4, end: first.start + 14,
    quote: first.quote.slice(4, 14) }));
  const lookups = [];
  const byId = new Map(corpus.map((d) => [d.id, d]));
  const reports = evidence.verifyExcerpts(saved, (id) => { lookups.push(id); return byId.get(id) || null; });
  assert.deepEqual(reports.map((r) => r.status), ["verified", "verified"]);
  assert.equal(lookups.length, 1);
});

test("malformed payloads are rejected before any lookup", () => {
  for (const payload of [null, [], "record", { unrelated: 1 }, { record: { schema: "x" }, content_sha256: "nope" }, { record: { schema: "x" }, content_sha256: 42 }]) {
    assert.throws(() => evidence.unwrapPacket(payload));
  }
  assert.equal(evidence.unwrapPacket({ schema: "x", hits: [] }).declared, null);
  assert.equal(evidence.unwrapPacket({ record: { schema: "x" }, content_sha256: "A".repeat(64) }).declared, "a".repeat(64));
});

test("without WebCrypto the fingerprint is reported as not computed, never as a mismatch", async () => {
  const saved = await evidence.packet(record(), webcrypto);
  const report = await evidence.verifyRecord(saved, null, { cryptoApi: {} });
  assert.equal(report.fingerprint.computed, null);
  assert.equal(report.fingerprint.matches, null);
  assert.deepEqual(report.excerpts, []);
  assert.equal(report.ok, true);
});

test("the Markdown report cites the fingerprint of its JSON twin", () => {
  const text = evidence.markdown(record(), { fingerprint: "ab".repeat(32) });
  assert.match(text, /## Verification/);
  assert.match(text, new RegExp("`" + "ab".repeat(32) + "`"));
  assert.match(text, /verify-record/);
  assert.doesNotMatch(evidence.markdown(record()), /## Verification/);
});
