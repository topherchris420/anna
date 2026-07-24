const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");

const html = fs.readFileSync("frontend/index.html", "utf8");
const app = fs.readFileSync("frontend/app.js", "utf8");
const css = fs.readFileSync("frontend/styles.css", "utf8");

function matchCount(value, pattern) {
  return (value.match(pattern) || []).length;
}

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
  assert.equal(
    matchCount(app, /EngineSearchRuntime\.createRuntime\(/g),
    1
  );
  assert.match(app, /runtime\.search\(/);
  assert.match(app, /runtime\.summarize\(/);
  assert.match(app, /runtime\.sources\(/);
  assert.match(app, /runtime\.retryLive\(/);
  assert.doesNotMatch(app, /\bgetJSON\b|\brefreshHealth\b/);
});

test("runtime initialization registers subscription and action exactly once", () => {
  assert.equal(matchCount(app, /runtime\.subscribe\(/g), 1);
  assert.equal(
    matchCount(
      app,
      /\$\("#runtime-action"\)\.addEventListener\("click", handleRuntimeAction\)/g
    ),
    1
  );
  const initStart = app.indexOf("function init()");
  assert.ok(initStart >= 0);
  const initBlock = app.slice(initStart);
  const order = [
    "readState();",
    "renderTree();",
    "renderWelcome();",
    "runtime.subscribe(applyRuntimeSnapshot);",
    '$("#runtime-action").addEventListener("click", handleRuntimeAction);',
    ".start()",
  ].map((source) => initBlock.indexOf(source));
  assert.ok(order.every((position) => position >= 0));
  assert.deepEqual(order, order.slice().sort((a, b) => a - b));
});

test("Demo presentation requires initialized Demo capabilities", () => {
  assert.match(
    app,
    /var demoActive = next\.provider === "demo" && hasCapabilities/
  );
  assert.match(
    app,
    /!hasCapabilities\s*\? "Lexical"\s*:\s*demoActive\s*\? "Demo lexical"\s*:\s*"Lexical \(BM25\)"/
  );
  assert.match(app, /notice\.hidden = !demoActive/);
  assert.match(
    app,
    /hasCapabilities &&\s*\(next\.provider === "live" \|\| next\.provider === "demo"\)/
  );
});

test("a connecting client error exposes Retry Live without claiming Demo", () => {
  assert.match(
    app,
    /var retryAvailable =\s*next\.phase === "demo" \|\|\s*\(next\.phase === "connecting" && !!next\.reason\)/
  );
  assert.match(app, /action\.hidden = !retryAvailable/);
});

test("uncited summaries never render answer text", () => {
  assert.match(
    app,
    /!Array\.isArray\(data\.citations\) \|\| !data\.citations\.length/
  );
});

test("provider changes and endpoint retries refresh the source catalog", () => {
  assert.match(
    app,
    /var providerChanged = runtimeSnapshot\.provider !== next\.provider/
  );
  assert.match(
    app,
    /if \(providerChanged\)\s*\{?\s*loadSourcesCatalog\(\)/
  );
  const saveStart = app.indexOf('$("#api-ok").addEventListener');
  const saveEnd = app.indexOf('$("#api-demo").addEventListener', saveStart);
  assert.ok(saveStart >= 0 && saveEnd > saveStart);
  const saveBlock = app.slice(saveStart, saveEnd);
  assert.match(
    saveBlock,
    /runtime\.retryLive\(\)\s*\.then\([\s\S]*loadSourcesCatalog\(\)/
  );
});

test("no-results guidance distinguishes Demo from lexical-only Live", () => {
  assert.match(app, /runtimeSnapshot\.provider === "demo"/);
  assert.match(app, /switch to Live Mode/);
  assert.match(app, /Lexical \(BM25\)/);
});

test("styles distinguish demo and recovery state without animation", () => {
  assert.match(css, /\.runtime-badge\.is-demo/);
  assert.match(css, /\.runtime-badge\.is-live/);
  assert.match(css, /\.runtime-badge\.is-reconnecting/);
  assert.match(css, /\.sr-only/);
  assert.doesNotMatch(
    css,
    /(?:^|[;{]\s*)(?:animation|transition)(?:-[\w-]+)?\s*:/m
  );
});
