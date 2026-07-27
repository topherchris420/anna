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

test("every interactive control is a real button or link", () => {
  // Facet toggles, example queries and filter chips are all operated by
  // keyboard users; a click handler on a <div> is not reachable by one.
  assert.match(app, /el\("button", "tree-node"/);
  assert.match(app, /node\.setAttribute\("aria-pressed"/);
  assert.match(app, /<button type="button" class="example"/);
  assert.match(app, /el\("button", "chip"\)/);
  assert.doesNotMatch(app, /el\("div", "tree-node/);
});

test("the menu bar is operable from the keyboard", () => {
  assert.match(html, /id="menubar" role="menubar"/);
  assert.match(html, /class="menu"[^>]*role="menuitem"[^>]*tabindex/);
  assert.match(html, /id="menu-popup"[^>]*role="menu"/);
  assert.match(app, /m\.addEventListener\("keydown", onMenuBarKey\)/);
  assert.match(app, /\$\("#menu-popup"\)\.addEventListener\("keydown", onPopupKey\)/);
  // Roving tabindex: one tab stop for the whole bar, arrows move inside it.
  assert.match(app, /m\.tabIndex = m === menuEl \? 0 : -1/);
  assert.match(app, /aria-expanded/);
});

test("a modal dialog traps Tab and restores focus to its opener", () => {
  assert.match(app, /dialogOpener = document\.activeElement/);
  assert.match(app, /if \(dialogOpener && document\.contains\(dialogOpener\)\) dialogOpener\.focus\(\)/);
  assert.match(app, /function trapDialogTab/);
  assert.match(app, /trapDialogTab\(e\)/);
  assert.match(html, /role="dialog" aria-modal="true" aria-labelledby="dialog-title"/);
});

test("results report busy state and counts to assistive technology", () => {
  assert.match(html, /id="results-announcer"[^>]*aria-live="polite"/);
  assert.match(app, /setAttribute\("aria-busy", "true"\)/);
  assert.equal(matchCount(app, /removeAttribute\("aria-busy"\)/g), 2);
  assert.match(app, /announce\(\s*data\.total \+ " result"/);
});

test("the page exposes landmarks and a skip link", () => {
  assert.match(html, /class="skip-link" href="#results"/);
  assert.match(html, /<main class="pane content">/);
  assert.match(html, /<aside class="pane sidebar" aria-label="Workspace Explorer">/);
});

test("applied filters are removable from beside the results", () => {
  assert.match(html, /id="active-filters"/);
  assert.match(app, /function renderActiveFilters/);
  assert.match(app, /renderActiveFilters\(\);/);
  assert.match(app, /chip\.setAttribute\("aria-label", "Remove filter "/);
  assert.match(app, /el\("button", "chip chip-clear", "Clear all"\)/);
});

test("hits without backend highlights still get a query-focused snippet", () => {
  assert.match(app, /function localSnippet/);
  assert.match(app, /demo\.snippet\(doc, demo\.tokens\(state\.q\)\)/);
});

test("styles keep focus visible and collapse to one column when narrow", () => {
  assert.match(css, /:focus-visible\s*\{[^}]*outline:/);
  assert.match(css, /\.skip-link/);
  assert.match(css, /@media \(max-width: 620px\)/);
  assert.match(css, /\.active-filters/);
  assert.match(css, /\.chip\b/);
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
