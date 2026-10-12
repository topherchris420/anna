const assert = require("node:assert/strict");
const test = require("node:test");
const citations = require("../../frontend/citations.js");
const corpus = require("../../frontend/demo-corpus.js");

const paper = {
  id: "arxiv:8bbe0f7de8c6440b", source: "arxiv", kind: "paper",
  title: "Zero-Copy Circular DMA Buffering for Sensor Acquisition",
  authors: ["M. Devlin", "R. Okafor", "S. Nakamura"], published: "2024-03-11T00:00:00Z",
  url: "https://arxiv.org/abs/2403.00001", pdf_url: "https://arxiv.org/pdf/2403.00001",
  identifiers: { arxiv_id: "2403.00001" },
};

test("a key is the first author's surname, the year and the first content word", () => {
  assert.equal(citations.bibtexKey(paper), "devlin2024zero");
  assert.equal(citations.surname("Smith, John"), "smith");
  assert.equal(citations.surname("Müller-Lüdenscheidt, K."), "mullerludenscheidt");
  assert.equal(citations.bibtexKey({ source: "espressif", title: "ESP32 DMA" }), "espressifndesp32");
  assert.equal(citations.titleWord("The Art of Electronics"), "art");
  assert.equal(citations.titleWord(""), "untitled");
  assert.equal(citations.yearOf("May 2023"), "");
});

test("an arXiv paper becomes an @article with eprint, link and the engine's id", () => {
  assert.equal(citations.bibtex(paper), [
    "@article{devlin2024zero,",
    "  author = {M. Devlin and R. Okafor and S. Nakamura},",
    "  title = {{Zero-Copy Circular DMA Buffering for Sensor Acquisition}},",
    "  journal = {arXiv preprint arXiv:2403.00001},",
    "  year = {2024},",
    "  eprint = {2403.00001},",
    "  archivePrefix = {arXiv},",
    "  url = {https://arxiv.org/abs/2403.00001},",
    "  annaid = {arxiv:8bbe0f7de8c6440b}",
    "}",
  ].join("\n"));
});

test("entry types follow the document kind and name the source", () => {
  const kinds = { paper: "@article", report: "@techreport", standard: "@techreport",
    documentation: "@manual", datasheet: "@manual", repository: "@misc", library: "@misc", other: "@misc" };
  Object.keys(kinds).forEach((kind) => {
    assert.ok(citations.bibtex(Object.assign({}, paper, { kind, source: "nasa" })).startsWith(kinds[kind] + "{"), kind);
  });
  assert.match(citations.bibtex(Object.assign({}, paper, { kind: "report", source: "nasa" })),
    /  institution = \{NASA Technical Reports \(NTRS\)\},/);
  assert.match(citations.bibtex(corpus[2]), /@manual\{espressifndesp32,\n  title = \{\{ESP32 DMA and Circular Buffers\}\},\n  organization = \{Espressif ESP-IDF Documentation\},\n  version = \{v5\.1\},/);
});

test("BibTeX special characters are escaped exactly once and names with 'and' are braced", () => {
  const doc = Object.assign({}, paper, {
    title: "100% of C&C {systems} cost $5 #1 a_b x^2 ~ \\ok",
    authors: ["Research and Development Corp"],
  });
  const entry = citations.bibtex(doc);
  assert.match(entry, /  title = \{\{100\\% of C\\&C \\\{systems\\\} cost \\\$5 \\#1 a\\_b x\\\^\{\}2 \\~\{\} \\textbackslash\{\}ok\}\},/);
  assert.match(entry, /  author = \{\{Research and Development Corp\}\},/);
  assert.equal(citations.escape("a  \n b"), "a b");
});

test("only http(s) links are cited, with braces percent-encoded", () => {
  assert.equal(citations.link({ url: "javascript:alert(1)" }), "");
  assert.equal(citations.link({ url: "", pdf_url: "https://x.org/p.pdf" }), "https://x.org/p.pdf");
  assert.equal(citations.link({ url: "https://x.org/a {b}\\c" }), "https://x.org/a%7Bb%7D%5Cc");
  assert.doesNotMatch(citations.bibtex(Object.assign({}, paper, { url: "ftp://x", pdf_url: "" })), /url =/);
});

test("plain references read as authors (year). Title. Source. Link", () => {
  assert.equal(citations.plain(paper),
    "M. Devlin, R. Okafor, and S. Nakamura (2024). Zero-Copy Circular DMA Buffering for Sensor Acquisition. arXiv. https://arxiv.org/abs/2403.00001");
  assert.equal(citations.plain(corpus[2]),
    "ESP32 DMA and Circular Buffers (n.d.). Espressif ESP-IDF Documentation, version v5.1. https://docs.espressif.com/projects/esp-idf/en/v5.1/esp32/");
  assert.equal(citations.plain(corpus[0]),
    "A. Researcher and B. Engineer (2023). Model Predictive Control of Quadrotor UAVs. arXiv. https://arxiv.org/abs/demo-1");
});

test("a bibliography keeps every key unique, in order", () => {
  const docs = [paper, Object.assign({}, paper, { id: "arxiv:2" }), Object.assign({}, paper, { id: "arxiv:3" })];
  assert.deepEqual(citations.citations(docs).map((c) => c.key), ["devlin2024zero", "devlin2024zerob", "devlin2024zeroc"]);
  const text = citations.bibliography(docs);
  assert.equal(text.split("@article{").length - 1, 3);
  assert.ok(text.endsWith("}\n"));
  assert.equal(citations.bibliography([]), "");
});

test("hostile or missing field types never throw and never leak", () => {
  const entry = citations.bibtex({ id: "x", title: null, authors: "nope", identifiers: ["a"], published: 2024, url: 42 });
  assert.equal(entry, "@misc{annanduntitled,\n  title = {{Untitled}},\n  annaid = {x}\n}");
  assert.equal(citations.citation({}).key, "annanduntitled");
});
