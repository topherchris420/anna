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
  // evidence.js precedes demo-search.js, which binds EngineEvidence at load.
  const order = [
    "config.js",
    "demo-corpus.js",
    "evidence.js",
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
    /!hasCapabilities\s*\? "Lexical"\s*:\s*demoActive\s*\? "Demo lexical"\s*:\s*modeLabel\("bm25"\)/
  );
  assert.match(app, /if \(next\.provider === "demo" && next\.capabilities\)/);
});

test("the landing states what is running, in the words a visitor reads", () => {
  // Live, unavailable and Demo each have one unmistakable sentence.
  assert.match(app, /"Anna is online · "/);
  assert.match(app, /"Anna's research backend is unavailable"/);
  assert.match(app, /Anna's research backend isn't reachable\.<\/b> Demo Mode remains available while the connection is restored\./);
  assert.match(app, /<b>Demo Mode<\/b> — searching/);
  assert.match(app, /This is not Anna's research index\./);
  // An unreachable backend offers Retry, Diagnostics and Switch to Demo.
  assert.match(app, /data-runtime-action="retry">Retry</);
  assert.match(app, /data-runtime-action="diagnostics">Diagnostics</);
  assert.match(app, /data-runtime-action="demo">Switch to Demo</);
  assert.match(app, /var retryAvailable = next\.phase === "unavailable" \|\| demoSelected/);
  assert.match(app, /action\.hidden = !retryAvailable/);
});

test("nothing enters Demo Mode except an explicit choice", () => {
  // The only calls into Demo are the user's: menu, toolbar, notices, dialog.
  assert.equal(matchCount(app, /runtime\.useDemo\(/g), 1);
  assert.match(app, /function chooseDemo\(\) \{\s*runtime\.useDemo\(\)/);
  const runtimeSource = fs.readFileSync("frontend/search-runtime.js", "utf8");
  // The runtime never calls demo.health() outside useDemo().
  assert.equal(matchCount(runtimeSource, /demo\.health\(/g), 1);
  assert.doesNotMatch(runtimeSource, /enterDemo/);
});

test("uncited summaries never render answer text", () => {
  assert.match(
    app,
    /!Array\.isArray\(data\.citations\) \|\| !data\.citations\.length/
  );
});

test("provider changes and returning to Live refresh sources and collections", () => {
  assert.match(
    app,
    /var providerChanged = runtimeSnapshot\.provider !== next\.provider/
  );
  assert.match(
    app,
    /if \(providerChanged\)\s*\{\s*loadSourcesCatalog\(\);\s*loadCollections\(\);/
  );
  assert.match(
    app,
    /\} else if \(becameLive\) \{\s*loadSourcesCatalog\(\);\s*loadCollections\(\);/
  );
  // A changed endpoint is probed afresh, never trusted from the old one.
  assert.match(app, /runtime\.useLive\(\{ fresh: true \}\)/);
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
  // Success, failure, and clearing the query all end the busy state.
  assert.equal(matchCount(app, /removeAttribute\("aria-busy"\)/g), 3);
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

test("hits show relevance on the shared 0–1 scale only when the backend reports it", () => {
  assert.match(app, /function scoreLabel\(hit\)/);
  assert.match(app, /if \(hit\.relevance != null\) return "relevance " \+ Number\(hit\.relevance\)\.toFixed\(2\)/);
  assert.match(app, /return "rank score "/);
  assert.match(app, /explainHit\(hit\)/);
});

test("hits without backend highlights still get a query-focused snippet", () => {
  assert.match(app, /function localSnippet/);
  assert.match(app, /demo\.snippet\(doc, demo\.tokens\(state\.q\)\)/);
});

test("styles keep focus visible and collapse to one column when narrow", () => {
  assert.match(css, /:focus-visible\s*\{[^}]*outline:/);
  // `.field:focus { outline: none }` outranks the bare :focus-visible rule,
  // so the search box needs its outline restored, not merely offset.
  assert.match(css, /\.field:focus-visible\s*\{[^}]*outline:\s*2px solid/);
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

test("Live Mode usability exposes interactive buttons and mode restoration", () => {
  assert.match(app, /id="notice-action-btn"/);
  assert.match(app, /id="try-live"/);
  assert.match(app, /Switch to Live Mode/);
  assert.match(app, /Switch to Demo Mode/);
  assert.match(app, /runtime\.useLive\(\)/);
  assert.match(app, /state\.mode = "hybrid"/);
  assert.match(css, /\.notice-btn/);
  assert.match(css, /\.runtime-badge\.is-unavailable/);
  assert.match(css, /\.runtime-badge\.is-waking/);
});

test("every advertised workflow is reachable from a result", () => {
  // Details (document + related), Save (collections) and Compare.
  assert.match(app, /data-hit-action="details"/);
  assert.match(app, /data-hit-action="save"/);
  assert.match(app, /data-hit-action="compare"/);
  assert.match(app, /runtime\.document\(id\)/);
  assert.match(app, /runtime\.related\(id\)/);
  assert.match(app, /runtime\.compare\(a\.id, b\.id\)/);
  assert.match(app, /runtime\.addBookmark\(/);
  assert.match(app, /runtime\.collections\(workspaceKey\(\)\)/);
  assert.match(html, /id="compare-tray"/);
  assert.doesNotMatch(app, /\bfetch\(/, "every backend call goes through the runtime");
});

test("title-bar buttons do what they show, or are not shown", () => {
  assert.match(html, /data-tb="explorer"[^>]*aria-label="Hide Workspace Explorer"/);
  assert.match(html, /data-tb="fullscreen"/);
  assert.match(html, /data-tb="close"[^>]*aria-label="Close search"/);
  assert.match(app, /document\.body\.classList\.toggle\("explorer-hidden"\)/);
  assert.match(app, /maximize\.hidden = true/);
  assert.match(css, /body\.explorer-hidden \.sidebar/);
});

test("the endpoint is configured in one place and only http(s) is accepted", () => {
  const config = fs.readFileSync("frontend/config.js", "utf8");
  assert.match(config, /var LOCAL_API_BASE = "http:\/\/localhost:8000"/);
  assert.match(config, /\/\^https\?:\$\/\.test\(url\.protocol\)/);
  assert.match(app, /config\.resolveApiBase\(\)\.base/);
  assert.doesNotMatch(app, /localStorage\.getItem\("engine_api_base"\)/);
});

test("a saved record can be verified from the File menu in Live or Demo mode", () => {
  assert.match(app, /\{ label: "Verify research record…", act: openVerifyDialog \}/);
  assert.match(app, /function openVerifyDialog\(\)/);
  assert.match(app, /id="verify-file" type="file" accept="\.json,application\/json" aria-label="Research record file"/);
  // The browser hashes locally; the selected provider re-reads the excerpts.
  assert.match(app, /evidence\.verifyRecord\(payload\)/);
  assert.match(app, /runtime\.verify\(payload\)/);
  assert.match(app, /this backend predates record verification/);
  assert.match(css, /\.verify-table/);
  assert.match(css, /\.verify-relocated td b/);
});

test("the Markdown export carries the fingerprint of its JSON twin", () => {
  assert.match(app, /evidence\.markdown\(record, \{ fingerprint: hash \}\)/);
});
