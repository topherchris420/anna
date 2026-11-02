/* End-to-end tests: the Anna workbench, driven like a user, against a REAL
 * backend (Flask + Postgres via deploy/entrypoint-free.sh, or docker-compose).
 *
 *   ANNA_E2E_API=http://localhost:8000 npm run test:e2e
 *
 * The backend must have the offline corpus loaded (`flask engine demo`,
 * which the free-tier entrypoint runs on boot). Outages are simulated in the
 * browser (request interception), so the backend itself is never stopped.
 * See docs/TESTING.md.
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { after, before, describe, test } = require("node:test");

const { chromium } = require("playwright");
const { startStaticServer } = require("./static-server");

// axe-core, the accessibility rule engine the accessibility test injects.
const AXE = (() => {
  try { return require.resolve("axe-core/axe.min.js"); } catch (error) { return null; }
})();

const API = (process.env.ANNA_E2E_API || "http://localhost:8000").replace(/\/+$/, "");
// The served directory: frontend/ by default; set ANNA_E2E_FRONTEND_DIR to test
// a build (dist/) exactly as a static host would serve it.
const FRONTEND_DIR = path.resolve(
  process.env.ANNA_E2E_FRONTEND_DIR || path.join(__dirname, "..", "..", "frontend")
);
// How long a state change may take. Local backends answer in milliseconds; a
// deployed free-tier backend may first need a minute to wake.
const WAIT_MS = Number(process.env.ANNA_E2E_WAIT_MS) || 20000;
// Timings a user never sees: shorter waits so outage paths finish quickly.
const FAST = { wakeBudgetMs: 4000, wakeRetryMs: 500, slowAfterMs: 300, retryDelays: [1000] };

let server;
let browser;

before(async () => {
  let health;
  try {
    health = await (await fetch(API + "/api/v1/health")).json();
  } catch (error) {
    throw new Error(
      "No Anna backend at " + API + " (" + error.message + "). Start one — " +
      "see docs/TESTING.md — or set ANNA_E2E_API."
    );
  }
  assert.equal(health.ready, true, "backend index is not ready: " + JSON.stringify(health));
  // Served with the static host's own headers — its Content-Security-Policy
  // included — so every test below runs the workbench under them.
  const host = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "frontend", "vercel.json"), "utf8"));
  const hostHeaders = {};
  host.headers.filter((rule) => rule.source === "/(.*)").forEach((rule) => {
    rule.headers.forEach((header) => { hostHeaders[header.key] = header.value; });
  });
  assert.ok(hostHeaders["Content-Security-Policy"], "frontend/vercel.json sets no Content-Security-Policy");
  server = await startStaticServer(FRONTEND_DIR, 0, hostHeaders);
  // Behind an egress proxy (e.g. testing a deployed backend from a sandbox),
  // set ANNA_E2E_BROWSER_PROXY; the local static server is always direct.
  const proxy = process.env.ANNA_E2E_BROWSER_PROXY;
  browser = await chromium.launch(
    // Chromium proxies loopback unless "<-loopback>" says otherwise.
    proxy ? { proxy: { server: proxy, bypass: "<-loopback>,127.0.0.1,localhost" } } : {}
  );
});

after(async () => {
  if (browser) await browser.close();
  if (server) await server.close();
});

/* One page in a fresh browser profile, with page errors and API traffic
   recorded so a test can assert on what actually went over the wire. */
async function openPage(options) {
  options = options || {};
  const context = options.context || (await browser.newContext());
  const page = await context.newPage();
  const errors = [];
  const api = [];
  page.on("pageerror", (error) => errors.push(error.message));
  // A Content-Security-Policy violation (the static host's, or a backend
  // page's) is reported on the console, and is as much a failure.
  page.on("console", (message) => {
    if (message.type() === "error" && /Content Security Policy/i.test(message.text())) {
      errors.push(message.text());
    }
  });
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/")) {
      api.push(request.method() + " " + request.url().replace(/^https?:\/\/[^/]+/, ""));
    }
  });
  if (options.tuning) {
    await page.addInitScript((tuning) => { window.ANNA_RUNTIME_TUNING = tuning; }, options.tuning);
  }
  if (options.beforeLoad) await options.beforeLoad(page);
  const params = new URLSearchParams();
  if (options.api !== false) params.set("api", API);
  if (options.q) params.set("q", options.q);
  Object.keys(options.extra || {}).forEach((key) => params.set(key, options.extra[key]));
  await page.goto(server.url + "?" + params.toString(), { waitUntil: "load" });
  return { page, context, errors, api };
}

const text = (page, selector) => page.$eval(selector, (node) => node.textContent.trim());
const badge = (page) => text(page, "#runtime-badge");

function waitForBadge(page, expected, timeout) {
  return page.waitForFunction(
    (value) => document.querySelector("#runtime-badge").textContent === value,
    expected,
    { timeout: timeout || WAIT_MS }
  );
}

function waitForSearchSettled(page) {
  return page.waitForFunction(
    () => !document.querySelector("#results").hasAttribute("aria-busy"),
    null,
    { timeout: Math.max(30000, WAIT_MS) }
  );
}

async function search(page, query) {
  await page.fill("#q", query);
  await page.press("#q", "Enter");
  await waitForSearchSettled(page);
}

function resultSources(page) {
  return page.$$eval("#results .result-window .rw-title > span:first-child", (nodes) =>
    nodes.map((node) => node.textContent.trim())
  );
}

describe("Anna workbench, end to end", () => {
  test("landing: the workbench connects to the real backend and says so", async () => {
    const { page, context, errors, api } = await openPage();
    await waitForBadge(page, "LIVE");
    assert.match(await text(page, "#conn-text"), /^Anna is online · \d+ documents$/);
    assert.equal(await page.$eval("#runtime-notice", (n) => n.hidden), true);
    assert.ok(api.some((call) => call === "GET /api/v1/health"), api.join("\n"));
    // Modes are labelled for what this backend actually runs.
    const modes = await page.$$eval("#mode option", (o) => o.map((x) => x.textContent));
    assert.ok(modes.every((label) => !/demo/i.test(label)), modes.join(", "));
    assert.deepEqual(errors, []);
    await context.close();
  });

  test("landing with no ?api= uses the local backend default (port 8000)", { skip: API !== "http://localhost:8000" }, async () => {
    const { page, context } = await openPage({ api: false });
    await waitForBadge(page, "LIVE");
    await context.close();
  });

  test("search: a real query returns documents only Anna's index holds", async () => {
    const { page, context, errors, api } = await openPage();
    await waitForBadge(page, "LIVE");
    await search(page, "library genesis");
    // The Shadow Libraries directory is not in the bundled Demo corpus, so
    // these hits can only have come from the backend.
    const sources = await resultSources(page);
    assert.ok(sources.includes("shadowlibraries"), "sources: " + sources.join(", "));
    assert.doesNotMatch(await text(page, "#status-metrics"), /demo-lexical/);
    assert.ok(api.some((call) => call.startsWith("GET /api/v1/search?q=library+genesis")), api.join("\n"));
    // Nonsense finds nothing — no padding the page with unrelated documents.
    await search(page, "zzqx plorfnog");
    assert.match(await text(page, "#results"), /Nothing found for “zzqx plorfnog”/);
    assert.deepEqual(errors, []);
    await context.close();
  });

  test("a shared deep-page link lands on real results, not an empty page", async () => {
    // The API clamps page to 50; a link past the last page must not show
    // "Nothing found" (or "Page 1000000 of 1") for a query that has results.
    const { page, context, errors } = await openPage({ q: "library genesis", extra: { page: "1000000" } });
    await waitForBadge(page, "LIVE");
    await waitForSearchSettled(page);
    assert.ok((await resultSources(page)).length > 0, await text(page, "#results"));
    assert.doesNotMatch(await text(page, "#pager"), /1000000/);
    assert.equal(new URL(page.url()).searchParams.get("page"), null, page.url());
    assert.deepEqual(errors, []);
    await context.close();
  });

  test("evidence: a result's source passage, ranking and record can be inspected", async () => {
    const { page, context, errors, api } = await openPage();
    await waitForBadge(page, "LIVE");
    await search(page, "circular buffer dma");
    await page.waitForSelector("#summary [data-cite]", { timeout: WAIT_MS });
    await page.click("#summary [data-cite]");
    const quote = await text(page, "#summary details.evidence-source[open] .evidence-quote");
    assert.match(quote, /circular/i);
    assert.ok(api.some((call) => call === "POST /api/v1/summarize"), api.join("\n"));

    await page.click("#results details.retrieval-detail summary");
    assert.match(await text(page, "#results details.retrieval-detail[open]"), /Ranking:/);

    // Open the record behind the first result.
    await page.click('#results [data-hit-action="details"]');
    await page.waitForSelector("#dialog-body .detail-table", { timeout: WAIT_MS });
    const dialog = await text(page, "#dialog-body");
    assert.match(dialog, /From Anna's research index/);
    assert.match(dialog, /Related documents/);
    assert.ok(api.some((call) => /^GET \/api\/v1\/document\/[^/]+$/.test(call)), api.join("\n"));
    assert.ok(api.some((call) => /^GET \/api\/v1\/document\/.+\/related/.test(call)), api.join("\n"));
    // Every document can be cited: the same BibTeX the backend serves.
    const bibtex = await text(page, "#dialog-body #cite-bibtex");
    assert.match(bibtex, /^@(article|techreport|manual|misc)\{[a-z0-9-]+,\n/);
    const docId = bibtex.match(/annaid = \{([^}]+)\}/)[1];
    const served = await fetch(API + "/api/v1/document/" + encodeURIComponent(docId) + "/citation");
    assert.equal(served.status, 200, "the backend at " + API + " predates GET /api/v1/document/<id>/citation");
    assert.equal(bibtex, (await served.json()).bibtex);
    await page.keyboard.press("Escape");

    // The research record can be saved once the source report settles.
    await page.waitForFunction(() => !document.querySelector("#export-json").disabled);
    const [download] = await Promise.all([page.waitForEvent("download"), page.click("#export-json")]);
    assert.equal(download.suggestedFilename(), "anna-research.json");
    const [references] = await Promise.all([page.waitForEvent("download"), page.click("#export-bibtex")]);
    assert.equal(references.suggestedFilename(), "anna-references.bib");
    assert.deepEqual(errors, []);
    await context.close();
  });

  test("compare: two selected results are compared by the backend", async () => {
    const { page, context, errors, api } = await openPage();
    await waitForBadge(page, "LIVE");
    await search(page, "anna's archive library");
    const buttons = await page.$$('#results [data-hit-action="compare"]');
    assert.ok(buttons.length >= 2, "need two results to compare");
    await buttons[0].click();
    await page.click('#results [data-hit-action="compare"] >> nth=1');
    await page.click("#compare-run");
    await page.waitForSelector("#dialog-body .compare-table", { timeout: WAIT_MS });
    const dialog = await text(page, "#dialog-body");
    assert.match(dialog, /From Anna's research index/);
    assert.match(dialog, /Text similarity: \d\.\d\d/);
    assert.ok(api.some((call) => call === "POST /api/v1/compare"), api.join("\n"));
    assert.deepEqual(errors, []);
    await context.close();
  });

  test("collections: a saved document persists across a reload", async () => {
    const context = await browser.newContext();
    const name = "E2E " + Date.now();
    const first = await openPage({ context });
    await waitForBadge(first.page, "LIVE");
    await first.page.waitForSelector("#tree >> text=New collection…");
    await search(first.page, "circular buffer dma");
    const title = await text(first.page, "#results .rw-heading");
    await first.page.click('#results [data-hit-action="save"]');
    await first.page.fill("#save-new-name", name);
    await first.page.click("#save-new-form button[type=submit]");
    await first.page.waitForFunction(() => /Saved\./.test(document.querySelector("#save-status").textContent), null, { timeout: WAIT_MS });
    await first.page.keyboard.press("Escape");
    assert.equal(await text(first.page, '#results [data-hit-action="save"]'), "★ Saved");
    await first.page.close();

    // A new page in the same browser profile: same workspace, same data.
    const second = await openPage({ context });
    await waitForBadge(second.page, "LIVE");
    const node = second.page.locator(".tree-node", { hasText: name });
    await node.waitFor({ timeout: WAIT_MS });
    assert.match(await node.textContent(), /1/);
    await node.click();
    assert.match(await text(second.page, "#dialog-body .bookmark-list"), new RegExp(title.slice(0, 20).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));

    // Clean up through the UI: remove the bookmark, then delete the collection.
    await second.page.click("#dialog-body [data-remove]");
    await second.page.waitForFunction(() => /Removed\./.test(document.querySelector("#collection-status").textContent));
    await second.page.click("#collection-delete");
    await second.page.click("#collection-delete");
    await second.page.waitForFunction((n) => !Array.from(document.querySelectorAll(".tree-node")).some((x) => x.textContent.includes(n)), name);
    assert.deepEqual(first.errors.concat(second.errors), []);
    await context.close();
  });

  test("backend failure mid-session: reported plainly, never answered from Demo, recovers", async () => {
    const { page, context, errors } = await openPage({ tuning: FAST });
    await waitForBadge(page, "LIVE");
    await page.route("**/api/v1/**", (route) => route.abort("connectionrefused"));
    await search(page, "circular buffer dma");
    assert.match(await text(page, "#results"), /Anna's research backend isn't reachable\./);
    assert.match(await text(page, "#results"), /Demo Mode remains available while the connection is restored\./);
    assert.notEqual(await badge(page), "LIVE");
    assert.notEqual(await badge(page), "DEMO");
    assert.doesNotMatch(await text(page, "#status-metrics"), /demo-lexical/);
    await waitForBadge(page, "OFFLINE");
    assert.match(await text(page, "#runtime-notice"), /Retry.*Diagnostics.*Switch to Demo/s);

    // The backend comes back: Retry restores Live and the search runs.
    await page.unroute("**/api/v1/**");
    await page.click('#runtime-notice [data-runtime-action="retry"]');
    await waitForBadge(page, "LIVE");
    await page.waitForSelector('#results [data-hit-action="details"]', { timeout: WAIT_MS });
    assert.deepEqual(errors, []);
    await context.close();
  });

  test("cold backend: a search waits for Anna instead of answering from Demo", async () => {
    let release;
    const healthy = new Promise((resolve) => { release = resolve; });
    const { page, context, errors } = await openPage({
      tuning: Object.assign({}, FAST, { wakeBudgetMs: 60000 }),
      beforeLoad: (p) => p.route("**/api/v1/health", async (route) => { await healthy; await route.continue(); }),
    });
    await waitForBadge(page, "WAKING");
    assert.match(await text(page, "#runtime-notice"), /Anna's research backend is starting up\./);
    await page.fill("#q", "circular buffer dma");
    await page.press("#q", "Enter");
    await page.waitForSelector("#results >> text=will run as soon as Anna is online");
    assert.equal(await page.$('#results [data-hit-action="details"]'), null);

    release();
    await waitForBadge(page, "LIVE");
    await waitForSearchSettled(page);
    assert.ok((await resultSources(page)).length > 0);
    assert.doesNotMatch(await text(page, "#status-metrics"), /demo-lexical/);
    assert.deepEqual(errors, []);
    await context.close();
  });

  test("backend down: unavailable state offers Retry, Diagnostics and Demo", async () => {
    const { page, context, errors } = await openPage({
      tuning: FAST,
      beforeLoad: (p) => p.route("**/api/v1/**", (route) => route.abort("connectionrefused")),
    });
    await waitForBadge(page, "OFFLINE");
    assert.equal(await text(page, "#conn-text"), "Anna's research backend is unavailable");
    assert.match(await text(page, "#runtime-notice"),
      /Anna's research backend isn't reachable\. Demo Mode remains available while the connection is restored\./);

    await page.click('#runtime-notice [data-runtime-action="diagnostics"]');
    const diagnostics = await text(page, "#dialog-body");
    assert.ok(diagnostics.includes(API), diagnostics);
    assert.match(diagnostics, /\?api= in the address/);
    assert.match(diagnostics, /Last health check.*unavailable/s);
    await page.keyboard.press("Escape");

    // Demo is a choice, and it is labelled as one.
    await page.click('#runtime-notice [data-runtime-action="demo"]');
    await waitForBadge(page, "DEMO");
    assert.match(await text(page, "#runtime-notice"), /Demo Mode — searching 3 bundled sample documents/);
    await search(page, "quadrotor");
    assert.match(await text(page, "#results"), /Model Predictive Control of Quadrotor UAVs/);
    assert.match(await text(page, "#status-metrics"), /demo-lexical/);
    assert.deepEqual(errors, []);
    await context.close();
  });

  test("demo mode: works on its own, says what it is, and hands back to Live", async () => {
    const { page, context, errors, api } = await openPage();
    await waitForBadge(page, "LIVE");
    await page.click('.menu[data-menu="edit"]');
    await page.click('#menu-popup .menu-item:has-text("Switch to Demo Mode")');
    await waitForBadge(page, "DEMO");
    assert.match(await text(page, "#conn-text"), /^Demo Mode · 3 bundled documents/);
    const before = api.length;
    await search(page, "esp32 dma");
    assert.match(await text(page, "#results"), /ESP32 DMA and Circular Buffers/);
    assert.ok(!api.slice(before).some((call) => call.startsWith("GET /api/v1/search")), "Demo search must not call the backend");
    // Demo details come from the bundled corpus, and say so.
    await page.click('#results [data-hit-action="details"]');
    await page.waitForSelector("#dialog-body .detail-table");
    assert.match(await text(page, "#dialog-body"), /From the bundled Demo corpus/);
    // Demo documents are citable too, offline, by the same rules.
    assert.match(await text(page, "#dialog-body #cite-bibtex"), /^@manual\{espressifndesp32,/);
    await page.keyboard.press("Escape");
    // Collections need the real backend; Demo says so instead of faking it.
    assert.match(await text(page, "#tree"), /available in Live Mode/);

    await page.click("#toolbar-live-btn");
    await waitForBadge(page, "LIVE");
    await waitForSearchSettled(page);
    assert.doesNotMatch(await text(page, "#status-metrics"), /demo-lexical/);
    assert.deepEqual(errors, []);
    await context.close();
  });

  test("workbench chrome: title bar, ingestion status and diagnostics are real", async () => {
    const { page, context, errors } = await openPage();
    await waitForBadge(page, "LIVE");
    await page.click('.tb-btn[data-tb="explorer"]');
    assert.equal(await page.$eval(".sidebar", (n) => getComputedStyle(n).display), "none");
    await page.click('.tb-btn[data-tb="explorer"]');
    assert.notEqual(await page.$eval(".sidebar", (n) => getComputedStyle(n).display), "none");

    await page.click('.menu[data-menu="ingestion"]');
    await page.click('#menu-popup .menu-item:has-text("Ingestion Status")');
    await page.waitForSelector("#ingest-sources .detail-table", { timeout: WAIT_MS });
    assert.match(await text(page, "#ingest-sources"), /shadowlibraries\s*\d+/);
    await page.keyboard.press("Escape");

    await page.click('.menu[data-menu="help"]');
    await page.click('#menu-popup .menu-item:has-text("Diagnostics")');
    assert.match(await text(page, "#dialog-body"), /Statuslive/);
    await page.keyboard.press("Escape");
    assert.deepEqual(errors, []);
    await context.close();
  });

  test("spelling: a misspelled word is corrected, says so, and what was typed is one click away", async () => {
    const { page, context, errors, api } = await openPage({ q: "circluar buffer" });
    await waitForBadge(page, "LIVE");
    await waitForSearchSettled(page);
    assert.match(await text(page, ".spelling-notice"),
      /^Showing results for circular buffer\. Search instead for circluar buffer$/);
    assert.equal(await text(page, ".spelling-notice b i"), "circular");
    assert.match(await text(page, "#results"), /ESP32 DMA and Circular Buffers/);

    await page.click('.spelling-notice [data-spell="0"]');
    await waitForSearchSettled(page);
    assert.equal(await page.$(".spelling-notice"), null);
    assert.match(page.url(), /[?&]spell=0(&|$)/);
    assert.ok(api.some((call) => /\/search\?.*q=circluar\+buffer.*&spell=0/.test(call)), api.join("\n"));

    // A new query from the Find box corrects again.
    await search(page, "librray genesis");
    assert.match(await text(page, ".spelling-notice"), /^Showing results for library genesis\./);
    assert.doesNotMatch(page.url(), /spell=0/);
    assert.deepEqual(errors, []);
    await context.close();
  });

  test("accessibility: no WCAG 2.1 A/AA violation on the workbench or the backend's pages", {
    // CI installs axe-core next to Playwright; a local run without it says how.
    skip: !AXE && !process.env.CI ? "axe-core not installed: npm install --no-save axe-core@4.10.2" : false,
  }, async () => {
    const violations = [];
    async function audit(page, where) {
      await page.addScriptTag({ path: AXE });
      const result = await page.evaluate(() => window.axe.run(document, {
        runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] },
      }));
      result.violations.forEach((v) => v.nodes.forEach((node) => {
        violations.push(where + ": " + v.id + " " + node.target.join(" ") + " — " +
          String(node.failureSummary || "").split("\n").slice(1).join(" ").slice(0, 160));
      }));
    }
    // axe is injected as an inline script, which the pages' CSP rightly
    // refuses; this one context sets the policy aside to audit them.
    const { page, context, errors } = await openPage({
      q: "circluar buffer", context: await browser.newContext({ bypassCSP: true }),
    });
    await waitForBadge(page, "LIVE");
    await waitForSearchSettled(page);
    await audit(page, "workbench results with a spelling correction");
    await page.click('#results [data-hit-action="details"]');
    await page.waitForSelector("#dialog-body .detail-table", { timeout: WAIT_MS });
    await audit(page, "workbench details dialog");
    await page.keyboard.press("Escape");

    const hit = (await (await fetch(API + "/api/v1/search?q=dma&per_page=1")).json()).hits[0];
    for (const path of ["/", "/search?q=dma", "/search?q=circluar+buffer", "/search?q=zzqxvb",
      "/document/" + encodeURIComponent(hit.document.id), "/collections", "/about", "/compare"]) {
      await page.goto(API + path, { waitUntil: "load" });
      await audit(page, path);
    }
    assert.deepEqual(violations, [], violations.join("\n"));
    assert.deepEqual(errors, []);
    await context.close();
  });

  test("phones: the workbench and the backend's own pages fit the screen", async () => {
    // A page wider than a phone's screen makes the phone zoom out to fit it
    // (the layout viewport grows past the device width) or scroll sideways.
    // 320 px is the narrowest phone in common use; 390 px a typical one.
    const hit = (await (await fetch(API + "/api/v1/search?q=dma&per_page=1")).json()).hits[0];
    const backendPages = [
      "/", "/search?q=dma", "/document/" + encodeURIComponent(hit.document.id),
      "/collections", "/about", "/compare",
    ];
    const width = (page) => page.evaluate(() => ({
      layout: window.innerWidth,
      content: document.documentElement.scrollWidth,
    }));
    for (const device of [320, 390]) {
      const context = await browser.newContext({
        viewport: { width: device, height: 740 }, isMobile: true, hasTouch: true,
      });
      const { page, errors } = await openPage({ context });
      await waitForBadge(page, "LIVE");
      await search(page, "dma");
      assert.deepEqual(await width(page), { layout: device, content: device }, "workbench @" + device);
      for (const path of backendPages) {
        const response = await page.goto(API + path, { waitUntil: "load" });
        assert.equal(response.status(), 200, path);
        assert.deepEqual(await width(page), { layout: device, content: device }, path + " @" + device);
      }
      assert.deepEqual(errors, []);
      await context.close();
    }
  });
});
