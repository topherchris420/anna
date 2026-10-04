const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync("frontend/config.js", "utf8");

/* Evaluate config.js as a page at ``href`` would, with ``saved`` in
   localStorage (or storage that throws, as in some private modes). */
function load(href, saved, storageThrows) {
  const store = new Map(saved ? [["engine_api_base", saved]] : []);
  const localStorage = {
    getItem: (k) => { if (storageThrows) throw new Error("denied"); return store.has(k) ? store.get(k) : null; },
    setItem: (k, v) => { if (storageThrows) throw new Error("denied"); store.set(k, String(v)); },
    removeItem: (k) => store.delete(k),
  };
  const url = new URL(href);
  const window = { location: url, localStorage };
  vm.runInNewContext(source, { window, URL, URLSearchParams });
  return { config: window.AnnaConfig, window, store };
}

// Objects built inside the vm context have that realm's prototypes.
function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

test("each kind of host gets the backend it is deployed with", () => {
  assert.equal(load("https://bethesdasearch.vercel.app/").config.resolveApiBase().base,
    "https://bethesdasearch-api.onrender.com");
  assert.equal(load("https://bethesdasearch.onrender.com/").config.resolveApiBase().base,
    "https://bethesdasearch-api.onrender.com");
  assert.equal(load("https://bethesdasearch-api.onrender.com/").config.resolveApiBase().base,
    "https://bethesdasearch-api.onrender.com");
  // docker-compose, ./run and the free-tier entrypoint all serve on 8000.
  assert.equal(load("http://localhost:5173/").config.resolveApiBase().base, "http://localhost:8000");
  assert.equal(load("http://127.0.0.1:8000/").config.resolveApiBase().base, "http://127.0.0.1:8000");
});

test("overrides apply in order: ?api=, then the saved setting, then the default", () => {
  const both = load("https://x.example/?api=https://q.example/api/v1/", "https://saved.example");
  assert.deepEqual(plain(both.config.resolveApiBase()), { base: "https://q.example", source: "query", rejected: [] });
  const saved = load("https://x.example/", "https://saved.example/");
  assert.equal(saved.config.resolveApiBase().source, "saved");
  assert.equal(saved.config.resolveApiBase().base, "https://saved.example");
});

test("only http(s) endpoints without credentials are accepted", () => {
  const { config } = load("https://x.example/");
  for (const bad of ["javascript:alert(1)", "data:text/html,x", "ftp://h.example", "https://u:p@h.example", "not a url", ""]) {
    assert.equal(config.normalizeApiBase(bad), "", bad);
  }
  assert.equal(config.normalizeApiBase(" https://h.example:8443/base/ "), "https://h.example:8443/base");
  const hostile = load("https://x.example/?api=javascript:alert(1)").config.resolveApiBase();
  assert.equal(hostile.source, "default");
  assert.equal(hostile.base, "https://bethesdasearch-api.onrender.com");
  assert.deepEqual(plain(hostile.rejected), [{ source: "query", value: "javascript:alert(1)" }]);
});

test("saving validates, and storage failures degrade to the default", () => {
  const { config, store } = load("https://x.example/");
  assert.equal(config.saveApiBase("javascript:alert(1)"), "");
  assert.equal(store.size, 0);
  assert.equal(config.saveApiBase("https://api.example/api/v1"), "https://api.example");
  assert.equal(store.get("engine_api_base"), "https://api.example");
  config.clearSavedApiBase();
  assert.equal(store.size, 0);
  const locked = load("https://x.example/", null, true).config;
  assert.equal(locked.resolveApiBase().source, "default");
  assert.equal(locked.saveApiBase("https://api.example"), "");
});
