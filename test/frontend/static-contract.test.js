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
