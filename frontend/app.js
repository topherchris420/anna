/* Anna · R.A.I.N. DataMatrix Engine — the research workbench (Win95 skin).
 * Framework-free SPA over the search runtime (search-runtime.js), which owns
 * every call to Anna's research backend. The endpoint comes from config.js
 * (AnnaConfig: ?api= override, saved setting, or the host default).
 *
 * What happens when you click Search: doSearch() → runtime.search() → (once
 * Live is ready) GET /api/v1/search → renderResults() → loadSummary() →
 * runtime.summarize() → POST /api/v1/summarize → the source report.
 */
(function () {
  "use strict";

  /* ------------------------------------------------------------------ API */
  var config = window.AnnaConfig;
  function apiBase() { return config.resolveApiBase().base; }
  function apiUrl(path) { return apiBase() + "/api/v1" + path; }

  /* -------------------------------------------------------------- helpers */
  function $(s) { return document.querySelector(s); }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function highlight(s) {
    return esc(s).replace(/&lt;em&gt;/g, "<em>").replace(/&lt;\/em&gt;/g, "</em>");
  }
  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  /* Result counts go to their own polite region: the runtime announcer speaks
     about Live/Demo state, and one overwriting the other would lose whichever
     came first. */
  function announce(text) { $("#results-announcer").textContent = text; }
  function isAbort(error) { return !!error && error.name === "AbortError"; }
  /* The specific reason, when it says more than the headline does. */
  var GENERIC_REASON = "Anna's research backend isn't reachable";
  function reasonSentence(reason) {
    return reason && reason !== GENERIC_REASON ? reason + ". " : "";
  }

  /* --------------------------------------------------------------- state */
  var MULTI = ["source", "kind", "category", "language"];
  var BOOL = ["has_code", "has_equations"];
  var YEARS = ["year_from", "year_to"];
  var CATEGORIES = [
    { label: "Academic Papers",   icon: "📄", values: ["paper"] },
    { label: "Technical Reports", icon: "📑", values: ["report"] },
    { label: "Silicon Datasheets",icon: "🔩", values: ["documentation", "datasheet"] },
    { label: "Source Code",       icon: "💾", values: ["repository", "code"] },
    { label: "Standards",         icon: "📐", values: ["standard"] },
    { label: "Shadow Libraries",  icon: "📚", values: ["library"] },
  ];
  var EXAMPLES = [
    "STM32 DMA circular buffer",
    "reinforcement learning for control",
    "RISC-V vector extension",
    "ESP32 deep sleep power",
    "finite element stress analysis",
  ];

  var state = { q: "", mode: "hybrid", page: 1, per_page: 20, filters: {} };
  var lastFacets = {};
  var lastHits = [];
  var searchGeneration = 0;
  // Re-run the current query once when Live returns after it failed for
  // availability (not on every return: a slow query must not loop).
  var rerunWhenLive = false;
  var researchRecord = null;
  var evidence = window.EngineEvidence;
  var sourcesCatalog = null;
  var compareSelection = [];
  var collections = { status: "idle", list: [], error: "" };
  var collectionsLoading = null; // the load in flight, for dialogs to wait on
  var demoProvider = window.EngineDemoSearch.createProvider(
    window.EngineDemoCorpus
  );
  var liveProvider = window.EngineSearchRuntime.createLiveProvider({
    getBaseUrl: apiBase,
    // Per attempt; a cold host is retried within the runtime's wake budget.
    healthTimeoutMs: 20000,
    requestTimeoutMs: 30000,
  });
  // Timings: probe again every 3 s for up to 150 s while a sleeping host
  // boots, then every 15/30/60 s in the background. End-to-end tests shorten
  // them by defining window.ANNA_RUNTIME_TUNING before this script runs.
  var tuning = window.ANNA_RUNTIME_TUNING || {};
  var runtime = window.EngineSearchRuntime.createRuntime({
    liveProvider: liveProvider,
    demoProvider: demoProvider,
    retryDelays: tuning.retryDelays || [15000, 30000, 60000],
    wakeBudgetMs: tuning.wakeBudgetMs,
    wakeRetryMs: tuning.wakeRetryMs,
    slowAfterMs: tuning.slowAfterMs,
  });
  var runtimeSnapshot = runtime.getSnapshot();

  function currentRequest() {
    var copiedFilters = {};
    Object.keys(state.filters).forEach(function (key) {
      copiedFilters[key] = Array.isArray(state.filters[key])
        ? state.filters[key].slice()
        : state.filters[key];
    });
    return {
      q: state.q,
      mode: state.mode,
      page: state.page,
      per_page: state.per_page,
      filters: copiedFilters,
    };
  }

  // The backend serves at most 50 pages (docs/API.md); a shared link may ask
  // for more, or for nonsense.
  var MAX_PAGE = 50;
  function validPage(value) {
    var page = parseInt(value, 10);
    return page >= 1 ? Math.min(page, MAX_PAGE) : 1;
  }
  function lastPage(total) {
    return Math.max(1, Math.min(MAX_PAGE, Math.ceil((total || 0) / state.per_page)));
  }

  function validYear(value) {
    var year = parseInt(value, 10);
    return year >= 1000 && year <= 9999 ? String(year) : "";
  }

  function readState() {
    var p = new URLSearchParams(location.search);
    state.q = p.get("q") || "";
    state.mode = ["hybrid", "bm25", "semantic"].indexOf(p.get("mode")) >= 0 ? p.get("mode") : "hybrid";
    state.page = validPage(p.get("page"));
    state.filters = {};
    MULTI.forEach(function (k) { var v = p.getAll(k); if (v.length) state.filters[k] = v; });
    BOOL.forEach(function (k) { if (p.get(k) === "true") state.filters[k] = "true"; });
    YEARS.forEach(function (k) { var y = validYear(p.get(k)); if (y) state.filters[k] = y; });
  }
  function buildQuery() {
    var p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    p.set("mode", state.mode);
    if (state.page > 1) p.set("page", String(state.page));
    p.set("per_page", String(state.per_page));
    MULTI.forEach(function (k) { (state.filters[k] || []).forEach(function (v) { p.append(k, v); }); });
    BOOL.forEach(function (k) { if (state.filters[k] === "true") p.set(k, "true"); });
    YEARS.forEach(function (k) { if (state.filters[k]) p.set(k, state.filters[k]); });
    return p;
  }
  function syncUrl() {
    var p = buildQuery(); p.delete("per_page");
    var api = new URLSearchParams(location.search).get("api");
    if (api) p.set("api", api);
    history.replaceState(null, "", "?" + p.toString());
  }

  /* ----------------------------------------------------------- filtering */
  function toggleMulti(param, value) {
    var arr = state.filters[param] || [];
    arr = arr.indexOf(value) >= 0 ? arr.filter(function (v) { return v !== value; }) : arr.concat([value]);
    if (arr.length) state.filters[param] = arr; else delete state.filters[param];
    state.page = 1; doSearch();
  }
  function toggleBool(param) {
    if (state.filters[param] === "true") delete state.filters[param]; else state.filters[param] = "true";
    state.page = 1; doSearch();
  }
  function setYears(from, to) {
    from = validYear(from); to = validYear(to);
    if (from && to && Number(from) > Number(to)) { var t = from; from = to; to = t; }
    if (from) state.filters.year_from = from; else delete state.filters.year_from;
    if (to) state.filters.year_to = to; else delete state.filters.year_to;
    state.page = 1; doSearch();
  }
  function categoryActive(cat) {
    var kinds = state.filters.kind || [];
    return cat.values.every(function (v) { return kinds.indexOf(v) >= 0; });
  }
  function toggleCategory(cat) {
    var kinds = (state.filters.kind || []).slice();
    if (categoryActive(cat)) {
      kinds = kinds.filter(function (v) { return cat.values.indexOf(v) < 0; });
    } else {
      cat.values.forEach(function (v) { if (kinds.indexOf(v) < 0) kinds.push(v); });
    }
    if (kinds.length) state.filters.kind = kinds; else delete state.filters.kind;
    state.page = 1; doSearch();
  }
  function clearFilters() { state.filters = {}; state.page = 1; doSearch(); }

  /* The sidebar shows what is *available*; this shows what is currently
     applied, so a filter that scrolled out of view is still visible — and
     removable — from where the results are. */
  var FILTER_LABELS = {
    source: "Source", kind: "Type", category: "Topic",
    language: "Language", has_code: "Has source code",
    has_equations: "Has equations", year_from: "From", year_to: "To",
  };
  function activeFilterList() {
    var list = [];
    MULTI.forEach(function (key) {
      (state.filters[key] || []).forEach(function (value) {
        list.push({
          label: FILTER_LABELS[key] + ": " + value,
          remove: function () { toggleMulti(key, value); },
        });
      });
    });
    BOOL.forEach(function (key) {
      if (state.filters[key] === "true") {
        list.push({
          label: FILTER_LABELS[key],
          remove: function () { toggleBool(key); },
        });
      }
    });
    YEARS.forEach(function (key) {
      if (state.filters[key]) {
        list.push({
          label: FILTER_LABELS[key] + " " + state.filters[key],
          remove: function () { delete state.filters[key]; state.page = 1; doSearch(); },
        });
      }
    });
    return list;
  }
  function renderActiveFilters() {
    var box = $("#active-filters");
    var list = activeFilterList();
    box.innerHTML = "";
    box.hidden = !list.length;
    if (!list.length) return;
    box.appendChild(el("span", "af-label", "Filters:"));
    list.forEach(function (item) {
      var chip = el("button", "chip");
      chip.type = "button";
      chip.innerHTML = esc(item.label) + '<span class="chip-x" aria-hidden="true">✕</span>';
      chip.setAttribute("aria-label", "Remove filter " + item.label);
      chip.addEventListener("click", item.remove);
      box.appendChild(chip);
    });
    var clear = el("button", "chip chip-clear", "Clear all");
    clear.type = "button";
    clear.addEventListener("click", clearFilters);
    box.appendChild(clear);
  }
  function newSearch() { state.q = ""; state.filters = {}; state.page = 1; $("#q").value = ""; doSearch(); $("#q").focus(); }

  /* -------------------------------------------------------------- search */
  function waitingForLive() {
    return runtimeSnapshot.provider === "live" && runtimeSnapshot.phase !== "live";
  }

  function doSearch() {
    var generation = ++searchGeneration;
    researchRecord = null;
    rerunWhenLive = false;
    $("#research-tools").hidden = true;
    setExportReady(false);
    syncUrl();
    $("#q").value = state.q;
    $("#mode").value = state.mode;
    renderActiveFilters();

    if (!state.q) {
      lastFacets = {};
      lastHits = [];
      renderTree();
      renderWelcome();
      $("#summary").hidden = true;
      $("#pager").innerHTML = "";
      $("#pane-count").textContent = "";
      $("#results").removeAttribute("aria-busy");
      setMetrics("Ready");
      return;
    }

    setMetrics("Searching…");
    $("#results").setAttribute("aria-busy", "true");
    $("#summary").hidden = true;
    $("#pager").innerHTML = "";
    if (waitingForLive()) {
      renderWaiting();
    } else {
      $("#results").innerHTML = '<div class="result-window"><div class="rw-title"><span>Working</span></div>' +
        '<div class="rw-body spinner-text">Executing ' + esc(modeLabel(state.mode)) + " retrieval…</div></div>";
    }

    var request = currentRequest();
    runtime.search(request)
      .then(function (data) {
        if (generation !== searchGeneration) return;
        // Show the page that was served, and never an empty page past the
        // end of a result set that has results: go to its last page.
        if (validPage(data.page) !== state.page && Number(data.page) >= 1) state.page = validPage(data.page);
        if (!(data.hits || []).length && data.total > 0 && state.page > lastPage(data.total)) {
          state.page = lastPage(data.total);
          doSearch();
          return;
        }
        syncUrl();
        lastFacets = data.facets || {};
        lastHits = data.hits || [];
        renderTree();
        renderResults(data);
        renderPager(data);
        setMetrics(data.total + " result" + (data.total === 1 ? "" : "s") + " · " +
          (data.took_ms || 0) + "ms · " + (data.mode || state.mode));
        $("#pane-count").textContent = "(" + data.total + ")";
        $("#results").removeAttribute("aria-busy");
        announce(
          data.total + " result" + (data.total === 1 ? "" : "s") +
          " for " + state.q +
          (data.mode === "demo-lexical" ? " in the Demo corpus" : "")
        );
        researchRecord = evidence.createRecord(request, data, null,
          data.mode === "demo-lexical" ? "demo" : "live");
        renderRetrieval(data.retrieval);
        loadSummary(data.hits || [], generation);
      })
      .catch(function (error) {
        if (generation !== searchGeneration || isAbort(error)) return;
        renderError(error);
      });
  }

  /* While Live is still coming up the query is queued, not answered from the
     Demo corpus; it runs the moment the backend is ready. */
  function renderWaiting() {
    if (!state.q) return;
    var waking = runtimeSnapshot.phase === "waking";
    $("#results").innerHTML = resultWindow("Waiting for Anna", "", "",
      '<div class="rw-heading">' + (waking
        ? "Anna's research backend is starting up."
        : "Connecting to Anna's research backend…") + "</div>" +
      "<p>Your search for “" + esc(state.q) + "” will run as soon as Anna is online.</p>" +
      (waking ? "<p>Free hosting puts the backend to sleep when it is idle; waking it usually takes under a minute.</p>" : "") +
      '<div class="rw-actions">' + actionButton("diagnostics", "Diagnostics") +
      actionButton("demo", "Use Demo Mode instead") + "</div>");
    bindActions($("#results"));
  }

  /* ---------------------------------------------------------- tree view */
  function treeNode(icon, label, active, count, onClick) {
    // A real <button>: every facet toggle has to be reachable and operable
    // from the keyboard, and aria-pressed is what announces its state.
    var node = el("button", "tree-node" + (active ? " active" : ""));
    node.type = "button";
    node.setAttribute("aria-pressed", active ? "true" : "false");
    node.innerHTML =
      '<span class="tn-icon" aria-hidden="true">' + icon + "</span>" +
      "<span>" + esc(label) + "</span>" +
      (count != null
        ? '<span class="tn-count">' + count +
          '<span class="sr-only"> results</span></span>'
        : "");
    node.addEventListener("click", onClick);
    return node;
  }
  function facetGroup(title, icon, param, buckets, limit) {
    var g = el("div", "tree-group");
    g.appendChild(el("div", "tree-root", "🗀 " + esc(title)));
    var active = state.filters[param] || [];
    // Keep an applied value visible even when it fell out of the top buckets.
    var shown = (buckets || []).slice(0, limit);
    active.forEach(function (v) {
      if (!shown.some(function (b) { return String(b.value) === v; })) shown.push({ value: v, count: null });
    });
    if (!shown.length) {
      g.appendChild(el("div", "tree-empty", "— run a search —"));
      return g;
    }
    shown.forEach(function (b) {
      var v = String(b.value);
      g.appendChild(treeNode(icon, v, active.indexOf(v) >= 0, b.count,
        function () { toggleMulti(param, v); }));
    });
    return g;
  }
  function renderTree() {
    var tree = $("#tree");
    tree.innerHTML = "";

    // Categories (map to the `kind` filter)
    var g1 = el("div", "tree-group");
    g1.appendChild(el("div", "tree-root", "🗀 Categories"));
    CATEGORIES.forEach(function (cat) {
      g1.appendChild(treeNode(cat.icon, cat.label, categoryActive(cat), null, function () { toggleCategory(cat); }));
    });
    tree.appendChild(g1);

    // Sources (live facet counts)
    tree.appendChild(facetGroup("Sources", "🔌", "source", lastFacets.source, 20));

    // Attributes
    var g3 = el("div", "tree-group");
    g3.appendChild(el("div", "tree-root", "🗀 Attributes"));
    var attrs = [
      { param: "has_code", label: "Has source code", icon: "≡" },
      { param: "has_equations", label: "Has equations", icon: "∑" },
    ];
    attrs.forEach(function (a) {
      var buckets = lastFacets[a.param] || [];
      var t = buckets.filter(function (b) { return b.value === true || b.value === 1 || b.value === "true"; })[0];
      var count = t ? t.count : null;
      g3.appendChild(treeNode(a.icon, a.label, state.filters[a.param] === "true", count,
        function () { toggleBool(a.param); }));
    });
    tree.appendChild(g3);

    // Topics and languages: the backends call the topic facet "categories"
    // (Live) or "category" (Demo); both filter with ?category=.
    var topics = lastFacets.categories || lastFacets.category || [];
    if (topics.length || (state.filters.category || []).length) {
      tree.appendChild(facetGroup("Topics", "🏷", "category", topics, 12));
    }
    if ((lastFacets.language || []).length || (state.filters.language || []).length) {
      tree.appendChild(facetGroup("Languages", "🌐", "language", lastFacets.language, 8));
    }

    tree.appendChild(yearGroup());
    tree.appendChild(collectionsGroup());
  }

  function yearGroup() {
    var g = el("div", "tree-group");
    g.appendChild(el("div", "tree-root", "🗀 Published"));
    var form = el("form", "year-form");
    form.innerHTML =
      '<label for="year-from">From</label><input class="field year-field" id="year-from" inputmode="numeric" ' +
        'pattern="[0-9]{4}" maxlength="4" placeholder="YYYY" value="' + esc(state.filters.year_from || "") + '">' +
      '<label for="year-to">to</label><input class="field year-field" id="year-to" inputmode="numeric" ' +
        'pattern="[0-9]{4}" maxlength="4" placeholder="YYYY" value="' + esc(state.filters.year_to || "") + '">' +
      '<button class="btn btn-small" type="submit">Apply</button>';
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      setYears(form.querySelector("#year-from").value, form.querySelector("#year-to").value);
    });
    g.appendChild(form);
    return g;
  }

  /* ------------------------------------------------------------- results */
  function resultWindow(titleLeft, kind, flagsHtml, bodyHtml) {
    return '<div class="result-window">' +
      '<div class="rw-title"><span>' + titleLeft + "</span>" +
      (flagsHtml || "") +
      (kind ? '<span class="rw-kind">' + esc(kind) + "</span>" : "") +
      "</div><div class=\"rw-body\">" + bodyHtml + "</div></div>";
  }

  /* A backend can return a hit with no highlights — an older index, a
     retriever that does not highlight, a match that landed in a field the
     highlighter does not cover. Showing the first 300 characters of the
     abstract in that case tells the reader nothing about why the document is
     on screen, so build the same kind of query-focused fragment locally,
     reusing the demo provider's snippet so both modes read alike. */
  function localSnippet(doc) {
    var demo = window.EngineDemoSearch;
    var fragments = demo.snippet(doc, demo.tokens(state.q));
    if (fragments.length) return highlight(fragments[0]);
    var abstract = doc.abstract || "";
    return esc(abstract.slice(0, 300)) + (abstract.length > 300 ? "…" : "");
  }

  function hitByIndex(index) { return lastHits[Number(index)]; }

  function renderResults(data) {
    if (!data.hits || !data.hits.length) {
      var capabilities = runtimeSnapshot.capabilities || {};
      var suggestion = runtimeSnapshot.provider === "demo"
        ? '<p>Demo Mode only holds ' + (capabilities.document_count || 3) + ' sample documents. Try a broader query, clear filters, or ' +
          '<button type="button" class="notice-btn" id="try-live">switch to Live Mode</button> to search Anna\'s research index.</p>'
        : capabilities.vector_search === true && state.mode === "bm25"
          ? '<p>Try a broader query, clear filters, or switch to ' +
            '<button type="button" class="notice-btn" id="try-semantic">' + esc(modeLabel("hybrid")) + "</button> retrieval.</p>"
          : "<p>Try a broader query, different words, or clear filters. Lexical (BM25) matching needs the query's terms to appear in the document.</p>";
      $("#results").innerHTML = resultWindow("No Results", "", "",
        '<div class="rw-heading">Nothing found for “' + esc(state.q) + '”.</div>' +
        suggestion);
      var t = $("#try-semantic");
      if (t) t.addEventListener("click", function () { $("#mode").value = "hybrid"; state.mode = "hybrid"; state.page = 1; doSearch(); });
      var tLive = $("#try-live");
      if (tLive) tLive.addEventListener("click", handleRuntimeAction);
      return;
    }
    var html = data.hits.map(function (hit, index) {
      var d = hit.document;
      var snippet = (hit.highlights && hit.highlights.length)
        ? highlight(hit.highlights[0])
        : localSnippet(d);
      var authors = (d.authors || []).slice(0, 4).join(", ") + ((d.authors || []).length > 4 ? " et al." : "");
      var meta = esc(authors) + (d.published ? (authors ? " · " : "") + esc(String(d.published).slice(0, 10)) : "");
      var tags = (d.categories || []).slice(0, 4).concat((d.tags || []).slice(0, 4))
        .map(function (x) { return '<span class="rw-tag">' + esc(x) + "</span>"; }).join("");
      var flags = (d.version ? '<span class="rw-flag">v:' + esc(d.version) + "</span>" : "") +
        (d.has_equations ? '<span class="rw-flag">∑</span>' : "") +
        (d.has_code ? '<span class="rw-flag">≡</span>' : "");
      var sourceUrl = evidence.safeUrl(d.url);
      var pdfUrl = evidence.safeUrl(d.pdf_url);
      var actions = hitActions(d, index);
      if (sourceUrl) actions.push('<a class="link" href="' + esc(sourceUrl) + '" target="_blank" rel="noopener noreferrer">Open source ↗</a>');
      if (pdfUrl) actions.push('<a class="link" href="' + esc(pdfUrl) + '" target="_blank" rel="noopener noreferrer">PDF ↗</a>');

      var body =
        '<div class="rw-heading">' +
          (sourceUrl ? '<a class="link" href="' + esc(sourceUrl) + '" target="_blank" rel="noopener noreferrer">' + esc(d.title) + "</a>" : esc(d.title)) +
        "</div>" +
        '<div class="rw-meta">' + (meta || "&nbsp;") +
          ' <span class="rw-score">· ' + scoreLabel(hit) + "</span></div>" +
        '<div class="rw-snippet">' + snippet + "</div>" +
        (tags ? '<div class="rw-tags">' + tags + "</div>" : "") +
        explainHit(hit) +
        '<div class="rw-actions">' + actions.join("") + "</div>";
      return resultWindow(esc(d.source), d.kind, flags, body);
    }).join("");
    $("#results").innerHTML = html;
    bindHitActions($("#results"));
  }

  /* Details, Save and Compare on every hit: real buttons wired to the same
     runtime as search, so they work in whichever mode is selected. */
  function hitActions(doc, index) {
    var selected = compareSelection.some(function (c) { return c.id === doc.id; });
    var saved = savedIn(doc.id).length > 0;
    return [
      '<button type="button" class="btn btn-small" data-hit-action="details" data-hit="' + index + '">Details</button>',
      '<button type="button" class="btn btn-small" data-hit-action="save" data-hit="' + index + '"' +
        (runtimeSnapshot.provider === "demo" ? ' aria-disabled="true" title="Collections are saved by Anna\'s research backend (Live Mode)"' : "") +
        ">" + (saved ? "★ Saved" : "☆ Save") + "</button>",
      '<button type="button" class="btn btn-small" data-hit-action="compare" data-hit="' + index + '" aria-pressed="' + selected + '">' +
        (selected ? "✓ Comparing" : "Compare") + "</button>",
    ];
  }
  function bindHitActions(container) {
    container.querySelectorAll("[data-hit-action]").forEach(function (button) {
      button.addEventListener("click", function () {
        var hit = hitByIndex(button.dataset.hit);
        if (!hit) return;
        var action = button.dataset.hitAction;
        if (action === "details") openDetails(hit.document.id);
        else if (action === "save") openSaveDialog(hit.document);
        else if (action === "compare") toggleCompare(hit.document);
      });
    });
  }

  function renderWelcome() {
    var ex = EXAMPLES.map(function (q) {
      return '<button type="button" class="example" data-ex="' + esc(q) + '">' +
        esc(q) + "</button>";
    }).join("");
    $("#results").innerHTML = resultWindow("Getting Started", "readme", "",
      '<div class="welcome-body">' +
        "<h2>Anna · R.A.I.N. DataMatrix Engine</h2>" +
        "<p>Hybrid lexical + vector search over research papers, standards, source code, " +
        "and vendor documentation, with citation-first answers. Type a query in the toolbar, " +
        "or filter categories in the Workspace Explorer.</p>" +
        "<p>On any result: <b>Details</b> shows the full record and related work, <b>Compare</b> " +
        "puts two documents side by side, and <b>Save</b> keeps it in a collection.</p>" +
        '<div class="rw-heading">Example queries</div>' +
        '<div class="example-list">' + ex + "</div>" +
      "</div>");
    $("#results").querySelectorAll("[data-ex]").forEach(function (n) {
      n.addEventListener("click", function () { state.q = n.dataset.ex; state.page = 1; state.filters = {}; doSearch(); });
    });
  }

  function actionButton(action, label) {
    return '<button type="button" class="btn" data-runtime-action="' + action + '">' + esc(label) + "</button>";
  }
  function bindActions(container) {
    container.querySelectorAll("[data-runtime-action]").forEach(function (button) {
      button.addEventListener("click", function () {
        var action = button.dataset.runtimeAction;
        if (action === "retry") retryLive();
        else if (action === "diagnostics") openDiagnostics();
        else if (action === "demo") chooseDemo();
        else if (action === "live") chooseLive();
      });
    });
  }

  /* A failed search says what failed and what can be done about it; an
     unreachable backend is never presented as an empty result. */
  function renderError(error) {
    lastFacets = {}; lastHits = []; renderTree();
    $("#results").removeAttribute("aria-busy");
    var api = window.EngineSearchRuntime;
    var down = !error || api.availabilityError(error) || error.code === "unavailable";
    var title, heading, detail;
    if (down) {
      rerunWhenLive = runtimeSnapshot.provider === "live" && !!state.q;
      title = "Backend Unavailable";
      heading = "Anna's research backend isn't reachable.";
      detail = "<p>Demo Mode remains available while the connection is restored. " +
        "This search will run again automatically when Anna is back online.</p>" +
        (reasonSentence(api.describeFailure(error)) ? '<p class="error-reason">' + esc(reasonSentence(api.describeFailure(error))) + "</p>" : "");
    } else if (error.code === "not-configured") {
      title = "No Backend Configured";
      heading = "No research backend is configured.";
      detail = "<p>Set the backend endpoint via <b>Edit ▸ API Endpoint…</b>.</p>";
    } else {
      title = "Request Failed";
      heading = "Anna's backend rejected this search.";
      detail = '<p class="error-reason">' + esc(error.message || String(error)) + "</p>";
    }
    $("#results").innerHTML = resultWindow(title, "error", "",
      '<div class="rw-heading">' + heading + "</div>" + detail +
      '<div class="rw-actions">' + actionButton("retry", "Retry") + actionButton("diagnostics", "Diagnostics") +
      (runtimeSnapshot.provider === "demo" ? "" : actionButton("demo", "Switch to Demo")) + "</div>");
    bindActions($("#results"));
    $("#summary").hidden = true;
    $("#pager").innerHTML = "";
    setMetrics("Error");
    announce("Search failed. " + heading);
  }

  function renderPager(data) {
    var pages = lastPage(data.total);
    var box = $("#pager");
    box.innerHTML = "";
    if (data.total <= state.per_page) return;
    if (state.page > 1) {
      var prev = el("button", "btn", "◄ Prev");
      prev.type = "button";
      prev.setAttribute("aria-label", "Previous page, page " + (state.page - 1));
      prev.addEventListener("click", function () { state.page--; doSearch(); $("#content-scroll").scrollTop = 0; });
      box.appendChild(prev);
    }
    box.appendChild(el("span", "pg-info", "Page " + state.page + " of " + pages));
    if (state.page < pages && data.hits.length) {
      var next = el("button", "btn", "Next ►");
      next.type = "button";
      next.setAttribute("aria-label", "Next page, page " + (state.page + 1));
      next.addEventListener("click", function () { state.page++; doSearch(); $("#content-scroll").scrollTop = 0; });
      box.appendChild(next);
    }
  }

  function setExportReady(ready) {
    $("#export-markdown").disabled = !ready;
    $("#export-json").disabled = !ready;
  }

  function renderRetrieval(report) {
    $("#research-tools").hidden = false;
    var text = "Retrieval details unavailable";
    if (report && report.executed) {
      text = report.executed.join(" + ") + " · " + report.candidate_count + " candidates";
      if (report.embedding === "hashing") text += " · term-hashing vectors (no semantic model)";
      if (report.degraded) text += " · unavailable: " + (report.unavailable || []).join(", ");
    }
    $("#retrieval-status").textContent = text;
  }

  /* Relevance is the hit's fused score as a share of the best score the
     retrievers that ran could assign (1.00 = ranked first by every one of
     them). Backends without that scale fall back to the raw ranking score.
     Neither is a probability that the document is correct. */
  function scoreLabel(hit) {
    if (hit.relevance != null) return "relevance " + Number(hit.relevance).toFixed(2);
    return "rank score " + (hit.score != null ? Number(hit.score).toFixed(4) : "");
  }

  function explainHit(hit) {
    var explanation = hit.explanation;
    if (!explanation || !explanation.method) return "";
    var ranks = explanation.ranks || {};
    var details = Object.keys(ranks).map(function (name) {
      var contribution = (explanation.contributions || {})[name];
      return esc(name) + " rank <b>" + esc(ranks[name]) + "</b>" +
        (contribution != null ? " · contribution " + Number(contribution).toFixed(6) : "");
    });
    if (hit.relevance != null) {
      details.push("Relevance <b>" + Number(hit.relevance).toFixed(2) + "</b> = fused score ÷ the best score " +
        "possible for the retrievers that ran (1.00 = first in every list).");
    }
    if (explanation.matched_terms) details.push("Matching terms: " + esc(explanation.matched_terms.join(", ")));
    return '<details class="retrieval-detail"><summary>Why this result?</summary><div>' +
      '<p>Ranking: ' + esc(explanation.method) + ". Scores order results; they are not probabilities of correctness.</p>" +
      details.map(function (line) { return "<p>" + line + "</p>"; }).join("") + "</div></details>";
  }

  function exportResearch(format) {
    if (!researchRecord) return;
    var record = JSON.parse(JSON.stringify(researchRecord));
    var task = format === "json"
      ? evidence.packet(record).then(function (packet) { return JSON.stringify(packet, null, 2) + "\n"; })
      : evidence.fingerprint(record)
          .then(function (hash) { return evidence.markdown(record, { fingerprint: hash }); })
          .catch(function () { return evidence.markdown(record); });
    task.then(function (content) {
      var blob = new Blob([content], { type: format === "json" ? "application/json" : "text/markdown;charset=utf-8" });
      var url = URL.createObjectURL(blob);
      var link = document.createElement("a");
      link.href = url; link.download = "anna-research." + (format === "json" ? "json" : "md");
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      announce("Research record saved as " + format + ".");
    }).catch(function (error) { announce("Export failed. " + error.message); });
  }

  function loadSummary(hits, generation) {
    var box = $("#summary");
    box.hidden = false;
    box.innerHTML = '<div class="rw-title"><span>Source report</span></div>' +
      '<div class="rw-body spinner-text">Selecting source evidence…</div>';
    var task = hits.length ? runtime.summarize({
      query: state.q,
      documentIds: hits.slice(0, 8).map(function (hit) { return hit.document.id; }),
    }) : Promise.resolve({ query: state.q, answer: "", citations: [], grounding: "insufficient-evidence" });
    task.then(function (data) {
      if (generation !== searchGeneration) return;
      if (!data || data.error) throw new Error("Summary unavailable");
      var hasCitations = Array.isArray(data.citations) && data.citations.length > 0;
      // Never present unreferenced model prose as an answer.
      if (!Array.isArray(data.citations) || !data.citations.length) {
        data = { query: state.q, answer: "No query-matching source excerpts were found. Try a more specific query or different sources.",
          citations: [], generator: "none", grounding: "insufficient-evidence" };
      }
      researchRecord.summary = JSON.parse(JSON.stringify(data));
      setExportReady(true);
      var known = new Set((data.citations || []).map(function (c) { return Number(c.n); }));
      var ans = esc(data.answer || "").replace(/\[(\d+)\]/g, function (marker, n) {
        return known.has(Number(n)) ? '<a class="citation-jump" href="#evidence-' + Number(n) +
          '" data-cite="' + Number(n) + '" aria-label="Inspect source ' + Number(n) + '">[' + Number(n) + "]</a>" : marker;
      });
      var cites = (data.citations || []).map(function (c) {
        var url = evidence.safeUrl(c.url);
        var excerpts = (c.excerpts || []).map(function (e) {
          return '<blockquote class="evidence-quote">' + esc(e.quote) + '</blockquote>' +
            '<div class="evidence-meta">' + esc(e.field) + " · offsets " + esc(e.start) + "–" + esc(e.end) +
            " · " + esc(e.offset_unit || "unicode-code-points") + " · matched: " + esc((e.matched_terms || []).join(", ")) + "</div>";
        }).join("");
        return '<details class="evidence-source" id="evidence-' + Number(c.n) + '"><summary>[' + Number(c.n) + "] " +
          esc(c.title) + ' <span class="rw-tag">' + esc(c.source) + '</span></summary><div class="evidence-body">' +
          (excerpts || '<p>This backend did not return source excerpts.</p>') +
          '<div class="rw-actions"><button type="button" class="btn btn-small" data-cite-details="' + esc(c.id) + '">Details</button>' +
          (url ? '<a class="link" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">Open original source ↗</a>' : "") +
          "</div></div></details>";
      }).join("");
      var exact = data.grounding === "source-extract";
      var label = !hasCitations ? "No matching evidence" : exact ? "Source excerpts" : "Model answer · review sources";
      var note = !hasCitations ? "" : exact
        ? "Exact source excerpts selected by query terms. Source accuracy and completeness still require review."
        : "Citation references are checked by the updated backend; factual support is not verified. Inspect the sources.";
      if (data.fallback_reason) note += " Model output unavailable or citation checks failed; showing source excerpts.";
      box.innerHTML = '<div class="rw-title"><span>Source report</span><span class="rw-kind">' + esc(label) + '</span></div>' +
        '<div class="rw-body"><div class="ans-text">' + ans + '</div>' +
        (note ? '<p class="grounding-note">' + esc(note) + '</p>' : "") +
        (cites ? '<div class="ans-cites">' + cites + '</div>' : "") + '</div>';
      box.querySelectorAll("[data-cite]").forEach(function (link) {
        link.addEventListener("click", function (event) {
          event.preventDefault();
          var source = $("#evidence-" + link.dataset.cite);
          if (source) { source.open = true; source.querySelector("summary").focus(); source.scrollIntoView({ block: "nearest" }); }
        });
      });
      box.querySelectorAll("[data-cite-details]").forEach(function (button) {
        button.addEventListener("click", function () { openDetails(button.dataset.citeDetails); });
      });
    }).catch(function (error) {
      if (generation !== searchGeneration || isAbort(error)) return;
      box.innerHTML = '<div class="rw-title"><span>Source report unavailable</span></div>' +
        '<div class="rw-body">The source report could not be built (' + esc(error.message || "backend error") +
        "). Search results are still available, and you can save this search without a summary.</div>";
      setExportReady(true);
    });
  }

  /* ------------------------------------------------------- mode labels */
  /* Labels follow what the connected backend actually does: Postgres runs
     full-text search, not BM25, and term-hashing vectors are not semantic. */
  function modeLabel(mode) {
    var c = runtimeSnapshot.capabilities || {};
    var lexical = runtimeSnapshot.provider === "demo" ? "Demo lexical"
      : c.backend === "postgres" ? "Lexical (full-text)" : "Lexical (BM25)";
    if (mode === "bm25") return lexical;
    var lexicalName = c.backend === "postgres" ? "full-text" : "BM25";
    if (c.embedding === "hashing") {
      return mode === "hybrid" ? "Hybrid (" + lexicalName + " + term vectors)" : "Term vectors (not semantic)";
    }
    return mode === "hybrid" ? "Hybrid (" + lexicalName + " + Vector)" : "Semantic (Vector)";
  }
  function applyModeOptions(next) {
    var hasCapabilities = next.capabilities != null;
    var demoActive = next.provider === "demo" && hasCapabilities;
    var capabilities = next.capabilities || {};
    var vectorAvailable = capabilities.vector_search === true;
    var hybridOption = $('#mode option[value="hybrid"]');
    var semanticOption = $('#mode option[value="semantic"]');
    var lexicalOption = $('#mode option[value="bm25"]');
    hybridOption.disabled = hasCapabilities && !vectorAvailable;
    semanticOption.disabled = hasCapabilities && !vectorAvailable;
    lexicalOption.textContent = !hasCapabilities
      ? "Lexical"
      : demoActive
        ? "Demo lexical"
        : modeLabel("bm25");
    if (hasCapabilities && vectorAvailable) {
      hybridOption.textContent = modeLabel("hybrid");
      semanticOption.textContent = modeLabel("semantic");
    }
    return { hasCapabilities: hasCapabilities, demoActive: demoActive, vectorAvailable: vectorAvailable };
  }

  /* -------------------------------------------------------- status bar */
  function setConn(led, text) {
    $("#conn-led").className = "led led-" + led;
    $("#conn-text").textContent = text;
  }
  function setMetrics(text) { $("#status-metrics").textContent = text; }

  var BADGES = {
    connecting: ["CONNECTING", "is-connecting"],
    waking: ["WAKING", "is-waking"],
    reconnecting: ["RECONNECTING", "is-reconnecting"],
    live: ["LIVE", "is-live"],
    unavailable: ["OFFLINE", "is-unavailable"],
    demo: ["DEMO", "is-demo"],
  };
  var wakeTicker = null;

  function secondsSince(ms) { return ms ? Math.max(0, Math.round((Date.now() - ms) / 1000)) : 0; }
  function secondsUntil(ms) { return ms ? Math.max(0, Math.round((ms - Date.now()) / 1000)) : 0; }

  function connText(next) {
    var capabilities = next.capabilities || {};
    if (next.provider === "demo") {
      return "Demo Mode · " + (capabilities.document_count || 0) + " bundled documents" +
        (next.phase === "reconnecting" ? " · checking Anna…" : next.liveAvailable ? " · Anna is online" : "");
    }
    switch (next.phase) {
      case "live": return "Anna is online · " + capabilities.document_count + " documents";
      case "waking": return "Waking Anna's research backend… " + secondsSince(next.wakeStartedAt) + "s";
      case "reconnecting": return "Checking Anna's research backend…";
      case "unavailable": return "Anna's research backend is unavailable";
      default: return "Connecting to Anna's research backend…";
    }
  }

  function renderNotice(next) {
    var notice = $("#runtime-notice");
    var capabilities = next.capabilities || {};
    var html = "";
    var tone = "";
    if (next.provider === "demo" && next.capabilities) {
      tone = "is-demo";
      html = "<b>Demo Mode</b> — searching " + (capabilities.document_count || 0) +
        " bundled sample documents with simple keyword matching. This is not Anna's research index. " +
        (next.liveAvailable
          ? 'Anna is online — <button type="button" id="notice-action-btn" class="notice-btn">Switch to Live Mode</button>'
          : '<button type="button" id="notice-action-btn" class="notice-btn">Retry Live connection</button>');
    } else if (next.phase === "waking") {
      tone = "is-waking";
      html = "<b>Anna's research backend is starting up.</b> Free hosting puts it to sleep when idle, so the first " +
        "visit can take up to a minute. Searches run as soon as Anna is online. " +
        '<button type="button" class="notice-btn" data-runtime-action="diagnostics">Diagnostics</button> · ' +
        '<button type="button" class="notice-btn" data-runtime-action="demo">Explore Demo Mode meanwhile</button>';
    } else if (next.phase === "unavailable") {
      tone = "is-unavailable";
      var retryIn = secondsUntil(next.nextRetryAt);
      html = "<b>Anna's research backend isn't reachable.</b> Demo Mode remains available while the connection is restored. " +
        '<span class="error-reason">' + esc(reasonSentence(next.reason)) +
        (retryIn ? "Retrying automatically in " + retryIn + "s." : "") + "</span> " +
        '<button type="button" class="notice-btn" data-runtime-action="retry">Retry</button> · ' +
        '<button type="button" class="notice-btn" data-runtime-action="diagnostics">Diagnostics</button> · ' +
        '<button type="button" class="notice-btn" data-runtime-action="demo">Switch to Demo</button>';
    }
    notice.className = "runtime-notice " + tone;
    notice.hidden = !html;
    if (!html) { notice.innerHTML = ""; return; }
    notice.innerHTML = html;
    var noticeBtn = $("#notice-action-btn");
    if (noticeBtn) noticeBtn.addEventListener("click", handleRuntimeAction);
    bindActions(notice);
  }

  function applyRuntimeSnapshot(next) {
    var previous = runtimeSnapshot;
    var providerChanged = runtimeSnapshot.provider !== next.provider;
    var previousProvider = runtimeSnapshot.provider;
    var becameLive = next.provider === "live" && next.phase === "live" && previous.phase !== "live";
    runtimeSnapshot = next;
    var badge = $("#runtime-badge");
    var action = $("#runtime-action");
    var demoSelected = next.provider === "demo";
    var b = demoSelected ? BADGES.demo : BADGES[next.phase] || BADGES.connecting;
    badge.className = "runtime-badge " + b[1];
    badge.textContent = demoSelected && next.phase === "reconnecting" ? "DEMO · CHECKING" : b[0];

    var toolbarBtn = $("#toolbar-live-btn");
    if (toolbarBtn) {
      toolbarBtn.textContent = next.provider === "live" ? "Switch to Demo" : "Switch to Live";
    }

    var modes = applyModeOptions(next);
    if (modes.hasCapabilities && !modes.vectorAvailable && state.mode !== "bm25") {
      state.mode = "bm25";
      $("#mode").value = "bm25";
      syncUrl();
    } else if (
      previousProvider === "demo" &&
      next.provider === "live" &&
      modes.vectorAvailable
    ) {
      state.mode = "hybrid";
      $("#mode").value = "hybrid";
      syncUrl();
    }

    var retryAvailable = next.phase === "unavailable" || demoSelected;
    action.hidden = !retryAvailable;
    action.textContent = demoSelected
      ? (next.liveAvailable ? "Switch to Live" : "Retry Live")
      : "Retry";
    renderNotice(next);

    $("#runtime-announcer").textContent =
      demoSelected
        ? "Demo Mode. Searching " + ((next.capabilities || {}).document_count || 0) + " bundled sample documents, not Anna's research index."
        : next.phase === "live"
          ? "Anna is online."
          : next.phase === "unavailable"
            ? "Anna's research backend is unavailable. " + (next.reason || "")
            : next.phase === "waking"
              ? "Anna's research backend is starting up."
              : "Connecting to Anna's research backend.";
    var led = next.phase === "live" && !demoSelected ? "green"
      : next.phase === "unavailable" && !demoSelected ? "red" : "yellow";
    setConn(led, connText(next));
    $("#engine-text").textContent =
      "Engine: " + (next.capabilities ? next.capabilities.retrieval : next.phase === "unavailable" ? "unavailable" : "checking");

    // Count the wake (and the next automatic retry) down where people look.
    var ticking = (next.phase === "waking" && !demoSelected) || (next.phase === "unavailable" && next.nextRetryAt);
    if (ticking && !wakeTicker) {
      wakeTicker = setInterval(function () {
        setConn(runtimeSnapshot.phase === "unavailable" ? "red" : "yellow", connText(runtimeSnapshot));
        if (runtimeSnapshot.phase === "unavailable") {
          var reason = $("#runtime-notice .error-reason");
          var retryIn = secondsUntil(runtimeSnapshot.nextRetryAt);
          if (reason) reason.textContent = reasonSentence(runtimeSnapshot.reason) + (retryIn ? "Retrying automatically in " + retryIn + "s." : "");
        }
      }, 1000);
    } else if (!ticking && wakeTicker) {
      clearInterval(wakeTicker);
      wakeTicker = null;
    }

    if (state.q && waitingForLive() && previous.phase !== next.phase &&
        $("#results").getAttribute("aria-busy") === "true") {
      renderWaiting();
    }
    if (providerChanged) {
      loadSourcesCatalog();
      loadCollections();
      if (state.q && previousProvider === "demo") doSearch();
    } else if (becameLive) {
      loadSourcesCatalog();
      loadCollections();
      if (rerunWhenLive && state.q) doSearch();
    } else if (next.provider === "live" && next.phase === "unavailable" && previous.phase !== "unavailable") {
      // Say so where these live, rather than "Loading…" forever.
      sourcesCatalog = [];
      collections = { status: "error", list: [], error: "Anna's research backend is unavailable" };
      renderTree();
    }
  }

  function retryLive() {
    setMetrics("Connecting to Anna's research backend…");
    runtime.retryLive().catch(function () {});
    if (state.q) doSearch(); // queued; runs when Anna answers
  }
  function chooseDemo() {
    runtime.useDemo().then(function () { doSearch(); }).catch(function () {});
  }
  function chooseLive() {
    runtime.useLive().catch(function () {});
  }

  function handleRuntimeAction() {
    var snapshot = runtime.getSnapshot();
    if (snapshot.provider === "demo") chooseLive();
    else if (snapshot.phase === "unavailable") retryLive();
    else if (snapshot.phase !== "live") openDiagnostics();
  }

  /* ---------------------------------------------------- compare */
  function toggleCompare(doc) {
    var index = compareSelection.map(function (c) { return c.id; }).indexOf(doc.id);
    if (index >= 0) {
      compareSelection.splice(index, 1);
      announce("Removed from comparison: " + doc.title);
    } else {
      if (compareSelection.length >= 2) compareSelection.shift();
      compareSelection.push({ id: doc.id, title: doc.title || doc.id });
      announce("Added to comparison: " + doc.title + ". " + compareSelection.length + " of 2 selected.");
    }
    renderCompareTray();
    refreshHitActions();
  }
  function refreshHitActions() {
    $("#results").querySelectorAll(".rw-actions").forEach(function (row) {
      var button = row.querySelector("[data-hit-action]");
      if (!button) return;
      var hit = hitByIndex(button.dataset.hit);
      if (!hit) return;
      var extras = Array.prototype.slice.call(row.querySelectorAll("a"));
      row.innerHTML = hitActions(hit.document, button.dataset.hit).join("");
      extras.forEach(function (a) { row.appendChild(a); });
      bindHitActions(row);
    });
  }
  function renderCompareTray() {
    var tray = $("#compare-tray");
    tray.hidden = !compareSelection.length;
    if (!compareSelection.length) { tray.innerHTML = ""; return; }
    tray.innerHTML = '<span class="af-label">Compare:</span>' +
      compareSelection.map(function (c, i) {
        return '<button type="button" class="chip" data-compare-remove="' + i + '" aria-label="Remove ' + esc(c.title) + ' from comparison">' +
          esc(c.title.length > 48 ? c.title.slice(0, 47) + "…" : c.title) + '<span class="chip-x" aria-hidden="true">✕</span></button>';
      }).join("") +
      (compareSelection.length < 2 ? '<span class="tray-hint">Select one more result to compare.</span>' : "") +
      '<button type="button" class="btn btn-small btn-default" id="compare-run"' + (compareSelection.length < 2 ? " disabled" : "") + ">Compare side by side</button>";
    tray.querySelectorAll("[data-compare-remove]").forEach(function (button) {
      button.addEventListener("click", function () {
        compareSelection.splice(Number(button.dataset.compareRemove), 1);
        renderCompareTray(); refreshHitActions();
      });
    });
    var run = $("#compare-run");
    if (run) run.addEventListener("click", openCompareDialog);
  }
  function openCompareDialog() {
    if (compareSelection.length < 2) return;
    var a = compareSelection[0], b = compareSelection[1];
    showDialog("Compare documents", '<p class="spinner-text">Comparing…</p>', null, "dialog-wide");
    runtime.compare(a.id, b.id).then(function (result) {
      if ($("#dialog-title").textContent !== "Compare documents") return;
      $("#dialog-body").innerHTML = renderComparison(result) + okBar();
      bindDialogClose();
      $("#dialog-body").querySelectorAll("[data-details]").forEach(function (button) {
        button.addEventListener("click", function () { openDetails(button.dataset.details); });
      });
    }).catch(function (error) {
      if (isAbort(error)) return;
      $("#dialog-body").innerHTML = '<p class="status-err">Comparison failed: ' + esc(error.message || String(error)) + "</p>" + okBar();
      bindDialogClose();
    });
  }
  function renderComparison(r) {
    function cell(doc) {
      var url = evidence.safeUrl(doc.url);
      return (url ? '<a class="link" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + esc(doc.title) + "</a>" : esc(doc.title)) +
        ' <button type="button" class="btn btn-small" data-details="' + esc(doc.id) + '">Details</button>';
    }
    function list(values) { return values && values.length ? values.map(esc).join(", ") : "—"; }
    function yes(v) { return v ? "yes" : "no"; }
    var rows = [
      ["Document", cell(r.a), cell(r.b)],
      ["Source", esc(r.a.source), esc(r.b.source)],
      ["Type", esc(r.a.kind), esc(r.b.kind)],
      ["Authors", list((r.a.authors || []).slice(0, 6)), list((r.b.authors || []).slice(0, 6))],
      ["Published", esc(String(r.a.published || "—").slice(0, 10)), esc(String(r.b.published || "—").slice(0, 10))],
      ["Version", esc(r.a.version || "—"), esc(r.b.version || "—")],
      ["Only in this one", list(r.only_a_categories), list(r.only_b_categories)],
      ["Source code / equations", yes(r.a.has_code) + " / " + yes(r.a.has_equations), yes(r.b.has_code) + " / " + yes(r.b.has_equations)],
    ];
    return '<p class="provenance">' + provenance() + "</p>" +
      '<table class="compare-table"><thead><tr><th scope="col"></th><th scope="col">A</th><th scope="col">B</th></tr></thead><tbody>' +
      rows.map(function (row) { return '<tr><th scope="row">' + row[0] + "</th><td>" + row[1] + "</td><td>" + row[2] + "</td></tr>"; }).join("") +
      "</tbody></table>" +
      "<p><b>Shared categories:</b> " + list(r.shared_categories) + "</p>" +
      "<p><b>Shared terms</b> (title and abstract, stop words excluded): " + list(r.shared_terms) + "</p>" +
      "<p><b>Text similarity:</b> " + Number(r.text_similarity).toFixed(2) +
      " — the share of their combined title/abstract vocabulary the two documents have in common (Jaccard). " +
      "It measures wording overlap, not agreement.</p>";
  }

  function provenance() {
    return runtimeSnapshot.provider === "demo"
      ? "From the bundled Demo corpus (sample data), not Anna's research index."
      : "From Anna's research index (" + esc((runtimeSnapshot.capabilities || {}).backend || "live") + ").";
  }

  /* ---------------------------------------------------- document details */
  function openDetails(id) {
    showDialog("Document details", '<p class="spinner-text">Loading ' + esc(id) + "…</p>", null, "dialog-wide");
    var docTask = runtime.document(id);
    var relatedTask = runtime.related(id).catch(function (error) {
      if (isAbort(error)) throw error;
      return { related: [], error: error };
    });
    Promise.all([docTask, relatedTask]).then(function (results) {
      if ($("#dialog-title").textContent !== "Document details") return;
      $("#dialog-body").innerHTML = renderDocument(results[0], results[1]) + okBar();
      bindDialogClose();
      var body = $("#dialog-body");
      body.querySelectorAll("[data-details]").forEach(function (button) {
        button.addEventListener("click", function () { openDetails(button.dataset.details); });
      });
      var doc = results[0];
      var save = body.querySelector("[data-doc-save]");
      if (save) save.addEventListener("click", function () { openSaveDialog(doc); });
      var cmp = body.querySelector("[data-doc-compare]");
      if (cmp) cmp.addEventListener("click", function () {
        toggleCompare(doc);
        cmp.textContent = compareSelection.some(function (c) { return c.id === doc.id; }) ? "✓ In comparison" : "Add to comparison";
        if (compareSelection.length === 2) { closeDialog(); openCompareDialog(); }
      });
      var first = body.querySelector("button, a");
      if (first) first.focus();
    }).catch(function (error) {
      if (isAbort(error)) return;
      $("#dialog-body").innerHTML = '<p class="status-err">' +
        (error.status === 404 ? "This document is not in the current index." : "Could not load the document: " + esc(error.message || String(error))) +
        "</p>" + okBar();
      bindDialogClose();
    });
  }
  function renderDocument(d, rel) {
    var url = evidence.safeUrl(d.url);
    var pdf = evidence.safeUrl(d.pdf_url);
    function row(label, value) { return value ? '<tr><th scope="row">' + label + "</th><td>" + value + "</td></tr>" : ""; }
    var ids = Object.keys(d.identifiers || {}).map(function (k) { return esc(k) + ": " + esc(d.identifiers[k]); }).join(", ");
    var body = String(d.body || "");
    var inComparison = compareSelection.some(function (c) { return c.id === d.id; });
    var related = (rel.related || []).map(function (hit) {
      var r = hit.document;
      return '<li><button type="button" class="link-button" data-details="' + esc(r.id) + '">' + esc(r.title) +
        '</button> <span class="rw-tag">' + esc(r.source) + "</span></li>";
    }).join("");
    return '<p class="provenance">' + provenance() + "</p>" +
      '<div class="rw-heading">' + esc(d.title) + "</div>" +
      '<table class="detail-table"><tbody>' +
        row("Source", esc(d.source) + " · " + esc(d.kind)) +
        row("Authors", esc((d.authors || []).join(", "))) +
        row("Published", esc(String(d.published || "").slice(0, 10))) +
        row("Version", esc(d.version || "")) +
        row("Language", esc(d.language || "")) +
        row("Categories", esc((d.categories || []).join(", "))) +
        row("Tags", esc((d.tags || []).join(", "))) +
        row("Identifiers", ids) +
        row("Contains", [d.has_code ? "source code" : "", d.has_equations ? "equations" : ""].filter(Boolean).join(", ")) +
        row("Document id", "<code>" + esc(d.id) + "</code>") +
      "</tbody></table>" +
      (d.abstract ? '<div class="detail-section"><b>Abstract</b><p class="detail-text">' + esc(d.abstract) + "</p></div>" : "") +
      (body ? '<div class="detail-section"><b>Indexed text</b> <span class="muted">(' + body.length.toLocaleString() + " characters" +
        (body.length > 2000 ? ", first 2,000 shown" : "") + ')</span><p class="detail-text">' + esc(body.slice(0, 2000)) +
        (body.length > 2000 ? "…" : "") + "</p></div>" : "") +
      '<div class="rw-actions">' +
        (url ? '<a class="link" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">Open source ↗</a>' : "") +
        (pdf ? '<a class="link" href="' + esc(pdf) + '" target="_blank" rel="noopener noreferrer">PDF ↗</a>' : "") +
        (runtimeSnapshot.provider === "demo" ? "" : '<button type="button" class="btn btn-small" data-doc-save>' + (savedIn(d.id).length ? "★ Saved" : "☆ Save") + "</button>") +
        '<button type="button" class="btn btn-small" data-doc-compare>' + (inComparison ? "✓ In comparison" : "Add to comparison") + "</button>" +
      "</div>" +
      '<div class="detail-section"><b>Related documents</b>' +
        (rel.error ? '<p class="status-err">Related documents unavailable: ' + esc(rel.error.message || "") + "</p>"
          : related ? "<ul class=\"related-list\">" + related + "</ul>"
          : "<p class=\"muted\">No related documents share this document's title terms.</p>") +
      "</div>";
  }

  /* --------------------------------------------------------- collections
     Saved on Anna's research backend under this browser's workspace key: a
     random, unguessable id (there are no accounts). Anyone holding the key
     can open the workspace, which is how it moves to another browser. */
  var WORKSPACE_KEY = "anna_workspace";
  var memoryWorkspace = null;
  function newWorkspaceKey() {
    var bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    return "ws_" + Array.prototype.map.call(bytes, function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
  }
  function validWorkspaceKey(value) { return /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/.test(String(value || "")); }
  function workspaceKey() {
    var key = null;
    try { key = window.localStorage.getItem(WORKSPACE_KEY); } catch (error) { key = null; }
    if (validWorkspaceKey(key)) return key;
    if (!memoryWorkspace) memoryWorkspace = newWorkspaceKey();
    try { window.localStorage.setItem(WORKSPACE_KEY, memoryWorkspace); } catch (error) { /* this visit only */ }
    return memoryWorkspace;
  }
  function workspacePersistent() {
    try { return window.localStorage.getItem(WORKSPACE_KEY) === workspaceKey(); } catch (error) { return false; }
  }

  function savedIn(documentId) {
    return collections.list.filter(function (c) {
      return (c.bookmarks || []).some(function (b) { return b.document_id === documentId; });
    });
  }

  function loadCollections() {
    if (runtimeSnapshot.provider === "demo") {
      collections = { status: "demo", list: [], error: "" };
      renderTree();
      return Promise.resolve();
    }
    collections = { status: "loading", list: collections.list, error: "" };
    renderTree();
    var task = runtime.collections(workspaceKey()).then(function (body) {
      collections = { status: "ready", list: body.collections, error: "" };
    }).catch(function (error) {
      if (isAbort(error)) return;
      collections = { status: "error", list: [], error: error.message || String(error) };
    }).then(function () {
      if (collectionsLoading === task) collectionsLoading = null;
      renderTree();
      refreshHitActions();
    });
    collectionsLoading = task;
    return task;
  }

  function collectionsGroup() {
    var g = el("div", "tree-group");
    g.appendChild(el("div", "tree-root", "🗀 Collections"));
    if (collections.status === "demo") {
      g.appendChild(el("div", "tree-empty", "Saved on Anna's research backend — available in Live Mode."));
      return g;
    }
    if (collections.status === "loading" && !collections.list.length) {
      g.appendChild(el("div", "tree-empty", "— loading —"));
    } else if (collections.status === "error") {
      g.appendChild(el("div", "tree-empty", "Unavailable: " + esc(collections.error)));
      g.appendChild(treeNode("↻", "Retry loading collections", false, null, loadCollections));
      return g;
    } else if (collections.status === "idle") {
      g.appendChild(el("div", "tree-empty", "— connecting —"));
      return g;
    }
    collections.list.forEach(function (c) {
      g.appendChild(treeNode("📁", c.name, false, (c.bookmarks || []).length, function () { openCollection(c.id); }));
    });
    g.appendChild(treeNode("＋", "New collection…", false, null, function () { openNewCollectionDialog(); }));
    return g;
  }

  function openNewCollectionDialog(thenSave) {
    showDialog("New collection",
      '<form id="new-collection-form"><div class="dialog-row"><label for="collection-name">Name</label>' +
      '<input class="field" id="collection-name" maxlength="200" required autocomplete="off"></div>' +
      '<div id="collection-status" role="status"></div>' +
      '<div class="dialog-actions"><button class="btn btn-default" type="submit">Create</button>' +
      '<button class="btn" type="button" data-close>Cancel</button></div></form>',
      function (body) {
        body.querySelector("#new-collection-form").addEventListener("submit", function (e) {
          e.preventDefault();
          var name = body.querySelector("#collection-name").value.trim();
          if (!name) return;
          body.querySelector("#collection-status").textContent = "Creating…";
          runtime.createCollection(workspaceKey(), name).then(function (created) {
            return loadCollections().then(function () {
              if (thenSave) return saveToCollection(thenSave, created.id).then(function () { closeDialog(); });
              closeDialog();
              announce("Collection " + name + " created.");
            });
          }).catch(function (error) {
            if (isAbort(error)) return;
            body.querySelector("#collection-status").innerHTML = '<p class="status-err">' + esc(error.message || String(error)) + "</p>";
          });
        });
      });
  }

  function saveToCollection(doc, collectionId) {
    return runtime.addBookmark(workspaceKey(), collectionId, doc).then(function () {
      announce("Saved " + doc.title + ".");
      return loadCollections();
    });
  }

  function openSaveDialog(doc) {
    if (runtimeSnapshot.provider === "demo") {
      showDialog("Save to collection",
        "<p>Collections are saved by Anna's research backend, so they are available in Live Mode only. " +
        "Demo Mode documents are bundled samples.</p>" + okBar());
      return;
    }
    if (collections.status !== "ready") {
      // Still loading (or not yet started): wait for it, then carry on.
      var pending = collectionsLoading || (collections.status === "error" ? null : loadCollections());
      if (pending) {
        showDialog("Save to collection", '<p class="spinner-text">Loading your collections…</p>' + okBar());
        pending.then(function () {
          if ($("#dialog-title").textContent !== "Save to collection") return;
          if (collections.status === "ready") openSaveDialog(doc);
          else $("#dialog-body").innerHTML = "<p>Collections are unavailable: " + esc(collections.error) + "</p>" + okBar();
          bindDialogClose();
        });
        return;
      }
      showDialog("Save to collection", "<p>Collections are unavailable: " + esc(collections.error) + "</p>" + okBar());
      loadCollections();
      return;
    }
    function render() {
      var saved = savedIn(doc.id).map(function (c) { return c.id; });
      return "<p>Save <b>" + esc(doc.title) + "</b> into:</p>" +
        (collections.list.length ? '<div class="collection-choices">' + collections.list.map(function (c) {
          var on = saved.indexOf(c.id) >= 0;
          return '<button type="button" class="btn collection-choice" aria-pressed="' + on + '" data-collection="' + Number(c.id) + '">' +
            (on ? "★ " : "☆ ") + esc(c.name) + ' <span class="muted">(' + (c.bookmarks || []).length + ")</span></button>";
        }).join("") + "</div>" : "<p class=\"muted\">No collections yet.</p>") +
        '<form id="save-new-form" class="dialog-row"><input class="field" id="save-new-name" maxlength="200" placeholder="New collection name" aria-label="New collection name" autocomplete="off">' +
        '<button class="btn" type="submit">Create and save</button></form>' +
        '<div id="save-status" role="status"></div>' + okBar();
    }
    function mount(body) {
      body.querySelectorAll("[data-collection]").forEach(function (button) {
        button.addEventListener("click", function () {
          var id = Number(button.dataset.collection);
          var on = button.getAttribute("aria-pressed") === "true";
          body.querySelector("#save-status").textContent = on ? "Removing…" : "Saving…";
          var task = on
            ? runtime.removeBookmark(workspaceKey(), id, doc.id).then(loadCollections)
            : saveToCollection(doc, id);
          task.then(function () {
            body.innerHTML = render();
            bindDialogClose();
            mount(body);
            body.querySelector("#save-status").textContent = on ? "Removed." : "Saved.";
          }).catch(function (error) {
            if (isAbort(error)) return;
            body.querySelector("#save-status").innerHTML = '<p class="status-err">' + esc(error.message || String(error)) + "</p>";
          });
        });
      });
      body.querySelector("#save-new-form").addEventListener("submit", function (e) {
        e.preventDefault();
        var name = body.querySelector("#save-new-name").value.trim();
        if (!name) return;
        body.querySelector("#save-status").textContent = "Creating…";
        runtime.createCollection(workspaceKey(), name).then(function (created) {
          return loadCollections().then(function () { return saveToCollection(doc, created.id); });
        }).then(function () {
          body.innerHTML = render();
          bindDialogClose();
          mount(body);
          body.querySelector("#save-status").textContent = "Saved.";
        }).catch(function (error) {
          if (isAbort(error)) return;
          body.querySelector("#save-status").innerHTML = '<p class="status-err">' + esc(error.message || String(error)) + "</p>";
        });
      });
    }
    showDialog("Save to collection", render(), mount);
  }

  function openCollection(id) {
    var c = collections.list.filter(function (x) { return x.id === id; })[0];
    if (!c) return;
    var armed = false;
    function render() {
      var marks = c.bookmarks || [];
      return '<p class="provenance">Workspace <code>' + esc(workspaceKey().slice(0, 11)) + "…</code> on Anna's research backend · " +
        marks.length + " saved document" + (marks.length === 1 ? "" : "s") + "</p>" +
        (marks.length ? '<ul class="bookmark-list">' + marks.map(function (m) {
          var url = evidence.safeUrl(m.url);
          return '<li><button type="button" class="link-button" data-details="' + esc(m.document_id) + '">' + esc(m.title || m.document_id) + "</button>" +
            (m.source ? ' <span class="rw-tag">' + esc(m.source) + "</span>" : "") +
            (url ? ' <a class="link" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">Open ↗</a>' : "") +
            ' <button type="button" class="btn btn-small" data-remove="' + esc(m.document_id) + '">Remove</button></li>';
        }).join("") + "</ul>" : "<p class=\"muted\">Nothing saved yet. Use ☆ Save on a search result.</p>") +
        '<div id="collection-status" role="status"></div>' +
        '<div class="dialog-actions"><button class="btn" type="button" id="collection-delete">' + (armed ? "Confirm: delete collection" : "Delete collection…") + "</button>" +
        '<button class="btn btn-default" type="button" data-close>OK</button></div>';
    }
    function mount(body) {
      body.querySelectorAll("[data-details]").forEach(function (button) {
        button.addEventListener("click", function () { openDetails(button.dataset.details); });
      });
      body.querySelectorAll("[data-remove]").forEach(function (button) {
        button.addEventListener("click", function () {
          runtime.removeBookmark(workspaceKey(), c.id, button.dataset.remove).then(loadCollections).then(function () {
            c = collections.list.filter(function (x) { return x.id === id; })[0] || c;
            body.innerHTML = render(); bindDialogClose(); mount(body);
            body.querySelector("#collection-status").textContent = "Removed.";
          }).catch(function (error) {
            if (isAbort(error)) return;
            body.querySelector("#collection-status").innerHTML = '<p class="status-err">' + esc(error.message || String(error)) + "</p>";
          });
        });
      });
      body.querySelector("#collection-delete").addEventListener("click", function () {
        if (!armed) { armed = true; body.innerHTML = render(); bindDialogClose(); mount(body); body.querySelector("#collection-delete").focus(); return; }
        runtime.deleteCollection(workspaceKey(), c.id).then(loadCollections).then(function () {
          closeDialog(); announce("Collection " + c.name + " deleted.");
        }).catch(function (error) {
          if (isAbort(error)) return;
          body.querySelector("#collection-status").innerHTML = '<p class="status-err">' + esc(error.message || String(error)) + "</p>";
        });
      });
    }
    showDialog("Collection: " + c.name, render(), mount, "dialog-wide");
  }

  function openWorkspaceDialog() {
    var key = workspaceKey();
    showDialog("Workspace key",
      "<p>Collections belong to this workspace key. There are no accounts: anyone who has the key can open and change this " +
      "workspace's collections, so share it only with people you trust.</p>" +
      '<div class="dialog-row"><input class="field" id="workspace-current" readonly value="' + esc(key) + '" aria-label="This workspace key">' +
      '<button class="btn" type="button" id="workspace-copy">Copy</button></div>' +
      (workspacePersistent() ? "" : '<p class="status-warn">This browser is not keeping site data, so this key lasts for this visit only. Copy it to keep your collections.</p>') +
      '<p>Open another workspace (for example the one from your other browser):</p>' +
      '<form id="workspace-form" class="dialog-row"><input class="field" id="workspace-new" maxlength="128" autocomplete="off" aria-label="Workspace key to open">' +
      '<button class="btn" type="submit">Open workspace</button></form>' +
      '<div id="workspace-status" role="status"></div>' + okBar(),
      function (body) {
        body.querySelector("#workspace-copy").addEventListener("click", function () {
          var done = function () { body.querySelector("#workspace-status").textContent = "Copied."; };
          if (navigator.clipboard) navigator.clipboard.writeText(key).then(done, function () { body.querySelector("#workspace-current").select(); });
          else body.querySelector("#workspace-current").select();
        });
        body.querySelector("#workspace-form").addEventListener("submit", function (e) {
          e.preventDefault();
          var value = body.querySelector("#workspace-new").value.trim();
          if (!validWorkspaceKey(value)) {
            body.querySelector("#workspace-status").innerHTML = '<p class="status-err">A workspace key is 1–128 letters, digits or . _ : @ - characters.</p>';
            return;
          }
          memoryWorkspace = value;
          try { window.localStorage.setItem(WORKSPACE_KEY, value); } catch (error) { /* this visit only */ }
          loadCollections();
          closeDialog();
          announce("Workspace opened.");
        });
      });
  }

  /* ------------------------------------------------------------- menus */
  var openMenu = null;
  function menuDefs() {
    return {
      file: [
        { label: "New Search", act: newSearch },
        { sep: true },
        { label: "Compare Selected…", act: openCompareDialog, disabled: compareSelection.length < 2 },
        { label: "Verify research record…", act: openVerifyDialog },
        { label: "Print Results…", act: function () { window.print(); } },
        { sep: true },
        { label: "Exit", act: function () { showDialog("Exit", "<p>Close the browser tab to exit Anna.</p>" + okBar()); } },
      ],
      edit: [
        { label: "Clear Filters", act: clearFilters },
        { sep: true },
        { label: "API Endpoint…", act: openApiDialog },
        { label: "Workspace Key…", act: openWorkspaceDialog },
        { sep: true },
        runtimeSnapshot.provider === "live"
          ? { label: "Switch to Demo Mode", act: chooseDemo }
          : runtimeSnapshot.liveAvailable
            ? { label: "Switch to Live Mode", act: handleRuntimeAction }
            : { label: "Retry Live Connection", act: handleRuntimeAction },
      ],
      ingestion: [
        { label: "Ingestion Status…", act: openIngestionDialog },
        { label: "How to Ingest…", act: openIngestHelp },
      ],
      sources: sourceMenu(),
      help: [
        { label: "Diagnostics…", act: openDiagnostics },
        { label: "REST API health (JSON) ↗", act: function () { window.open(apiUrl("/health"), "_blank", "noopener"); } },
        { label: "API Reference ↗", act: function () { window.open("https://github.com/topherchris420/anna/blob/main/docs/API.md", "_blank", "noopener"); } },
        { sep: true },
        { label: "About Anna…", act: openAbout },
      ],
    };
  }
  function sourceMenu() {
    var items = [{ label: "All Sources (clear)", act: function () { delete state.filters.source; state.page = 1; doSearch(); } }, { sep: true }];
    if (!sourcesCatalog) {
      items.push({ label: "Loading…", disabled: true });
    } else if (!sourcesCatalog.length) {
      items.push({ label: "Unavailable (backend offline)", disabled: true });
    } else {
      sourcesCatalog.forEach(function (s) {
        items.push({
          label: s.display_name || s.name,
          count: (state.filters.source || []).indexOf(s.name) >= 0 ? "✓" : "",
          act: function () { toggleMulti("source", s.name); },
        });
      });
    }
    return items;
  }

  function menuButtons() {
    return Array.prototype.slice.call(document.querySelectorAll(".menu"));
  }
  function focusMenuBar(menuEl) {
    // Roving tabindex: the menu bar is one tab stop, arrows move within it.
    menuButtons().forEach(function (m) {
      m.tabIndex = m === menuEl ? 0 : -1;
    });
    menuEl.focus();
  }
  function popupItems() {
    return Array.prototype.slice.call(
      document.querySelectorAll("#menu-popup .menu-item:not(.disabled)")
    );
  }
  function focusPopupItem(index) {
    var items = popupItems();
    if (!items.length) return;
    var item = items[(index + items.length) % items.length];
    items.forEach(function (i) { i.tabIndex = i === item ? 0 : -1; });
    item.focus();
  }
  function closeMenu(restoreFocus) {
    var pop = $("#menu-popup"); pop.hidden = true; pop.innerHTML = "";
    if (openMenu) {
      openMenu.classList.remove("open");
      openMenu.setAttribute("aria-expanded", "false");
      if (restoreFocus) openMenu.focus();
    }
    openMenu = null;
  }
  function showMenu(menuEl) {
    var defs = menuDefs()[menuEl.dataset.menu] || [];
    var pop = $("#menu-popup");
    pop.innerHTML = "";
    defs.forEach(function (item) {
      if (item.sep) {
        var sep = el("div", "menu-sep");
        sep.setAttribute("role", "separator");
        pop.appendChild(sep);
        return;
      }
      var mi = el("div", "menu-item" + (item.disabled ? " disabled" : ""),
        esc(item.label) + (item.count ? '<span class="mi-count">' + esc(item.count) + "</span>" : ""));
      mi.setAttribute("role", "menuitem");
      if (item.disabled) {
        mi.setAttribute("aria-disabled", "true");
      } else {
        mi.tabIndex = -1;
        mi.addEventListener("click", function () { closeMenu(true); item.act(); });
      }
      pop.appendChild(mi);
    });
    var r = menuEl.getBoundingClientRect();
    pop.style.left = r.left + "px";
    pop.style.top = r.bottom + "px";
    pop.hidden = false;
    if (openMenu && openMenu !== menuEl) {
      openMenu.classList.remove("open");
      openMenu.setAttribute("aria-expanded", "false");
    }
    openMenu = menuEl;
    menuEl.classList.add("open");
    menuEl.setAttribute("aria-expanded", "true");
  }
  function onMenuBarKey(e) {
    var buttons = menuButtons();
    var index = buttons.indexOf(e.currentTarget);
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      var next = buttons[
        (index + (e.key === "ArrowRight" ? 1 : -1) + buttons.length) %
          buttons.length
      ];
      focusMenuBar(next);
      if (openMenu) showMenu(next);
    } else if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
      showMenu(e.currentTarget);
      focusPopupItem(0);
    } else if (e.key === "Escape") {
      closeMenu(true);
    } else {
      return;
    }
    e.preventDefault();
  }
  function onPopupKey(e) {
    var items = popupItems();
    var index = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown") {
      focusPopupItem(index + 1);
    } else if (e.key === "ArrowUp") {
      focusPopupItem(index - 1);
    } else if (e.key === "Home") {
      focusPopupItem(0);
    } else if (e.key === "End") {
      focusPopupItem(items.length - 1);
    } else if (e.key === "Enter" || e.key === " ") {
      if (document.activeElement) document.activeElement.click();
    } else if (e.key === "Escape" || e.key === "ArrowLeft" || e.key === "ArrowRight") {
      var opener = openMenu;
      closeMenu(true);
      if (opener && e.key !== "Escape") {
        var buttons = menuButtons();
        var next = buttons[
          (buttons.indexOf(opener) + (e.key === "ArrowRight" ? 1 : -1) +
            buttons.length) % buttons.length
        ];
        focusMenuBar(next);
        showMenu(next);
        focusPopupItem(0);
      }
    } else {
      return;
    }
    e.preventDefault();
  }

  /* ----------------------------------------------------------- dialogs */
  function okBar() { return '<div class="dialog-actions"><button class="btn btn-default" data-close>OK</button></div>'; }
  var dialogOpener = null;
  function dialogFocusables() {
    return Array.prototype.slice.call(
      $("#dialog-overlay").querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      )
    ).filter(function (node) { return !node.disabled; });
  }
  function bindDialogClose() {
    $("#dialog-body").querySelectorAll("[data-close]").forEach(function (b) {
      b.addEventListener("click", closeDialog);
    });
  }
  function showDialog(title, bodyHtml, onMount, variant) {
    // Remember where focus came from: a modal that drops focus back to the
    // top of the document makes the keyboard user restart their journey.
    // A dialog opened from another dialog keeps the original opener.
    if ($("#dialog-overlay").hidden) dialogOpener = document.activeElement;
    $("#dialog-title").textContent = title;
    $("#dialog-body").innerHTML = bodyHtml;
    $(".dialog").className = "dialog" + (variant ? " " + variant : "");
    $("#dialog-overlay").hidden = false;
    bindDialogClose();
    if (onMount) onMount($("#dialog-body"));
    var focusables = dialogFocusables();
    if (focusables.length) focusables[0].focus();
  }
  function closeDialog() {
    if ($("#dialog-overlay").hidden) return;
    $("#dialog-overlay").hidden = true;
    $("#dialog-body").innerHTML = "";
    $("#dialog-title").textContent = "";
    if (dialogOpener && document.contains(dialogOpener)) dialogOpener.focus();
    dialogOpener = null;
  }
  /* Tab must not walk out of a modal dialog and into the page behind it. */
  function trapDialogTab(e) {
    if (e.key !== "Tab" || $("#dialog-overlay").hidden) return;
    var focusables = dialogFocusables();
    if (!focusables.length) return;
    var first = focusables[0];
    var last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      last.focus();
      e.preventDefault();
    } else if (!e.shiftKey && document.activeElement === last) {
      first.focus();
      e.preventDefault();
    }
  }

  function openAbout() {
    showDialog("About Anna",
      '<div style="display:flex;gap:12px">' +
        '<div style="font-size:32px">⚙</div><div>' +
        "<div class=\"rw-heading\">Anna · R.A.I.N. DataMatrix Engine</div>" +
        "<p>Engineering answers you can cite: hybrid lexical + vector retrieval over papers, " +
        "standards, source code and vendor documentation, with inspectable source evidence.</p>" +
        "<p>A Vers3Dynamics project. Open source, self-hostable, air-gap friendly.</p>" +
        '<p style="color:#404040">Backend: <code>' + esc(apiBase() || "(unset)") + "</code></p>" +
        "</div></div>" + okBar());
  }
  function openApiDialog() {
    var resolved = config.resolveApiBase();
    showDialog("API Endpoint",
      "<p>Backend base URL (scheme + host, no <code>/api/v1</code>):</p>" +
      '<div class="dialog-row"><input class="field" id="api-input" type="url" value="' + esc(resolved.base) + '" aria-label="Backend base URL"></div>' +
      '<p class="muted">Default for this site: <code>' + esc(config.defaultApiBase()) + "</code>" +
      (resolved.source === "query" ? " · currently overridden by <code>?api=</code> in the address" : "") + "</p>" +
      '<div class="dialog-row"><button class="btn" id="api-test">Test</button><span id="api-status" role="status"></span></div>' +
      '<div class="dialog-actions">' +
      '<button class="btn btn-default" id="api-ok">Save and Retry Live</button>' +
      '<button class="btn" id="api-reset">Use default</button>' +
      '<button class="btn" id="api-demo">Use Demo</button>' +
      '<button class="btn" data-close>Cancel</button></div>',
      function () {
        $("#api-test").addEventListener("click", function () {
          var status = $("#api-status");
          var base = config.normalizeApiBase($("#api-input").value);
          if (!base) {
            status.textContent = "✕ Enter an http(s) URL";
            status.className = "status-err";
            return;
          }
          var probe = window.EngineSearchRuntime.createLiveProvider({
            getBaseUrl: function () { return base; },
            healthTimeoutMs: 20000,
            requestTimeoutMs: 20000,
          });
          status.textContent = "Testing…";
          status.className = "";
          probe.health().then(function (health) {
            status.textContent = health.ready
              ? "✓ Connected · " + health.document_count + " docs"
              : "Backend responded but its index is not ready";
            status.className = health.ready ? "status-ok" : "status-err";
          }).catch(function (error) {
            status.textContent = "✕ " + window.EngineSearchRuntime.describeFailure(error);
            status.className = "status-err";
          });
        });
        function applyEndpoint() {
          closeDialog();
          runtime.useLive({ fresh: true }).catch(function () {});
          if (state.q) doSearch();
        }
        $("#api-ok").addEventListener("click", function () {
          if (!config.saveApiBase($("#api-input").value)) {
            $("#api-status").textContent = "✕ Enter an http(s) URL";
            $("#api-status").className = "status-err";
            return;
          }
          applyEndpoint();
        });
        $("#api-reset").addEventListener("click", function () {
          config.clearSavedApiBase();
          applyEndpoint();
        });
        $("#api-demo").addEventListener("click", function () {
          closeDialog();
          chooseDemo();
        });
      });
  }

  /* ---------------------------------------------------------- diagnostics */
  function diagnosticsReport() {
    var s = runtime.getSnapshot();
    var resolved = config.resolveApiBase();
    var c = s.capabilities || {};
    var probe = s.lastProbe;
    var mixed = location.protocol === "https:" && /^http:/.test(resolved.base) &&
      !/^http:\/\/(localhost|127\.0\.0\.1)(:|\/|$)/.test(resolved.base);
    var SOURCES = { query: "?api= in the address", saved: "saved in this browser (Edit ▸ API Endpoint…)", default: "default for " + location.host };
    return [
      ["Status", s.provider === "demo" ? "Demo Mode (selected)" : s.phase],
      ["Reason", s.reason || "—"],
      ["API endpoint", resolved.base || "(none)"],
      ["Endpoint from", SOURCES[resolved.source] || resolved.source],
      ["Ignored overrides", resolved.rejected.length ? resolved.rejected.map(function (r) { return r.source + ": " + r.value; }).join("; ") + " (not an http(s) URL)" : "—"],
      ["Mixed content", mixed ? "BLOCKED: this https page cannot call an http backend" : "ok"],
      ["Browser online", navigator.onLine === false ? "no" : "yes"],
      ["Last health check", probe ? new Date(probe.at).toLocaleTimeString() + " · " + probe.ms + " ms · " +
        (probe.ok ? (probe.ready ? "ready" : "index not ready") : (probe.code + (probe.status ? " (HTTP " + probe.status + ")" : "") + ": " + probe.message)) : "—"],
      ["Backend", c.provider === "live" ? (c.backend + " · " + c.document_count + " documents · retrieval " + c.retrieval) : "—"],
      ["Vectors", c.provider === "live" ? (c.vector_search ? c.embedding + (c.semantic_search ? " (semantic)" : " (not semantic)") : "none (full-text only)") : "—"],
      ["Workspace", workspaceKey().slice(0, 11) + "…" + (workspacePersistent() ? "" : " (this visit only)")],
      ["Page", location.origin],
    ];
  }
  function openDiagnostics() {
    function table() {
      return '<table class="detail-table"><tbody>' + diagnosticsReport().map(function (r) {
        return '<tr><th scope="row">' + esc(r[0]) + "</th><td>" + esc(r[1]) + "</td></tr>";
      }).join("") + "</tbody></table>";
    }
    showDialog("Diagnostics",
      '<div id="diag-table">' + table() + "</div>" +
      '<div class="dialog-row"><button class="btn" type="button" id="diag-run">Check the backend now</button>' +
      '<button class="btn" type="button" id="diag-copy">Copy report</button><span id="diag-status" role="status"></span></div>' +
      (runtimeSnapshot.phase === "unavailable" ? '<p class="muted">Free hosting sleeps when idle: a backend that has been asleep can take a minute to answer. ' +
        "If it never answers, check the endpoint above and that the backend allows this page's origin (CORS).</p>" : "") +
      okBar(),
      function (body) {
        body.querySelector("#diag-run").addEventListener("click", function () {
          body.querySelector("#diag-status").textContent = "Checking…";
          var refresh = function () {
            if (!body.isConnected) return;
            body.querySelector("#diag-table").innerHTML = table();
            body.querySelector("#diag-status").textContent = "Done.";
          };
          (runtimeSnapshot.provider === "demo" ? runtime.retryLive() : runtime.retryLive().then(function (s) {
            if (s.phase === "live" && state.q && rerunWhenLive) doSearch();
            return s;
          })).then(refresh, refresh);
        });
        body.querySelector("#diag-copy").addEventListener("click", function () {
          var text = diagnosticsReport().map(function (r) { return r[0] + ": " + r[1]; }).join("\n");
          if (navigator.clipboard) {
            navigator.clipboard.writeText(text).then(function () { body.querySelector("#diag-status").textContent = "Copied."; },
              function () { body.querySelector("#diag-status").textContent = "Copy failed."; });
          }
        });
      });
  }

  /* ---------------------------------------------------- record verification
     Reads a saved .json export, recomputes its fingerprint in the browser,
     then asks the selected provider to re-read every cited excerpt: the
     backend index in Live Mode, the bundled corpus in Demo Mode. */
  var STATUS_TEXT = {
    "verified": "still at the recorded offsets",
    "relocated": "still present, offsets have moved",
    "drifted": "passage no longer in the document",
    "missing-document": "document not in this index",
    "missing-field": "document has no such field",
    "invalid-excerpt": "malformed excerpt, not checked",
  };
  function openVerifyDialog() {
    showDialog("Verify research record",
      "<p>Choose a <b>Save evidence .json</b> export. Its fingerprint is recomputed here, " +
      "and every cited excerpt is re-read from the " +
      (runtimeSnapshot.provider === "live" ? "Live backend index." : "bundled Demo corpus.") + "</p>" +
      '<div class="dialog-row"><input class="field" id="verify-file" type="file" accept=".json,application/json" aria-label="Research record file"></div>' +
      '<div id="verify-result" class="verify-result" role="status" aria-live="polite"></div>' +
      '<div class="dialog-actions"><button class="btn btn-default" id="verify-run">Verify</button>' +
      '<button class="btn" data-close>Close</button></div>',
      function (body) {
        body.querySelector("#verify-run").addEventListener("click", function () {
          var input = body.querySelector("#verify-file");
          var file = input.files && input.files[0];
          var out = body.querySelector("#verify-result");
          if (!file) { out.innerHTML = '<p class="status-err">Choose a file first.</p>'; return; }
          out.innerHTML = '<p class="spinner-text">Reading ' + esc(file.name) + "…</p>";
          file.text().then(function (text) {
            var payload = JSON.parse(text);
            var local = evidence.verifyRecord(payload);
            return Promise.all([local, runtime.verify(payload).then(
              function (remote) { return { report: remote }; },
              function (error) { if (error && error.name === "AbortError") throw error; return { error: error }; })]);
          }).then(function (results) {
            renderVerification(out, results[0], results[1]);
          }).catch(function (error) {
            if (error && error.name === "AbortError") return;
            out.innerHTML = '<p class="status-err">' + esc(error instanceof SyntaxError ? "Not a JSON file." : (error.message || String(error))) + "</p>";
          });
        });
      });
  }
  function renderVerification(out, local, remote) {
    var fp = local.fingerprint;
    var byBackend = "";
    if (fp.computed == null && remote.report && remote.report.fingerprint) {
      fp = remote.report.fingerprint; // this browser cannot hash (plain HTTP); the backend did
      byBackend = " (computed by the backend)";
    }
    var lines = [];
    if (fp.computed == null) {
      lines.push('<p class="status-warn">Fingerprint not computed here (needs HTTPS or localhost).</p>');
    } else if (fp.matches === true) {
      lines.push('<p class="status-ok">Fingerprint matches' + byBackend + ": the record is unchanged since export.</p>");
    } else if (fp.matches === false) {
      lines.push('<p class="status-err">Fingerprint mismatch' + byBackend + ": the record content changed after export.</p>");
    } else {
      lines.push("<p>No fingerprint declared (bare record); computed " + esc(fp.computed.slice(0, 16)) + "…" + byBackend + ".</p>");
    }
    if (remote.error) {
      lines.push('<p class="status-err">Excerpts not re-checked: ' + esc(remote.error.message || String(remote.error)) +
        (remote.error.status === 404 ? " (this backend predates record verification)." : "") + "</p>");
    } else {
      var report = remote.report;
      var against = report.checked_against || {};
      var counts = report.counts || {};
      lines.push("<p>Excerpts re-read from <b>" + esc(against.backend || "?") + "</b> / " + esc(against.index || "?") + ": " +
        Object.keys(counts).filter(function (k) { return counts[k]; }).map(function (k) { return counts[k] + " " + esc(k); }).join(", ") +
        (report.excerpts.length ? "" : "no excerpts to check") + ".</p>");
      if (report.excerpts.length) {
        lines.push('<table class="verify-table"><thead><tr><th scope="col">Cite</th><th scope="col">Document</th><th scope="col">Where</th><th scope="col">Status</th></tr></thead><tbody>' +
          report.excerpts.map(function (e) {
            var where = esc(e.field) + " " + esc(e.start) + "–" + esc(e.end) + (e.status === "relocated" ? " → " + esc(e.found_at) : "");
            return '<tr class="verify-' + esc(e.status) + '"><td>[' + esc(e.citation) + "]</td><td>" + esc(e.document_id) + "</td><td>" + where +
              "</td><td><b>" + esc(e.status) + "</b> · " + esc(STATUS_TEXT[e.status] || "") + (e.detail ? " (" + esc(e.detail) + ")" : "") + "</td></tr>";
          }).join("") + "</tbody></table>");
      }
      lines.push("<p>" + (report.ok && fp.matches !== false
        ? '<span class="status-ok">Record verified.</span>'
        : '<span class="status-err">Record not fully verified.</span>') +
        " A match means the quotations still exist where the record says; it does not establish that a source is correct.</p>");
    }
    out.innerHTML = lines.join("");
  }

  function openIngestionDialog() {
    var snapshot = runtime.getSnapshot();
    var capabilities = snapshot.capabilities || {};
    var ready = capabilities.ready === true;
    showDialog("Ingestion Status",
      '<table class="detail-table"><tbody>' +
      row("Provider", snapshot.provider === "demo" ? "Demo (bundled sample corpus)" : "Live (Anna's research backend)") +
      row("Backend", esc(capabilities.backend || "?")) +
      row("Documents", String(capabilities.document_count || 0)) +
      row("Retrieval", esc(capabilities.retrieval || "?")) +
      row("Vector search", capabilities.vector_search
        ? "available" + (capabilities.embedding === "hashing" ? " (term-hashing vectors, not semantic)" : capabilities.semantic_search ? " (semantic model)" : "")
        : "unavailable") +
      row("Status", '<span class="' + (ready ? "status-ok" : "status-err") + '">' + esc(snapshot.provider === "demo" ? "demo" : snapshot.phase || "?") + "</span>") +
      "</tbody></table>" +
      '<div class="detail-section"><b>Documents by source</b><div id="ingest-sources"><p class="spinner-text">Counting…</p></div></div>' +
      okBar(), function (body) {
        // An empty query browses the whole index; its source facet is the
        // per-source document count.
        runtime.browse({ q: "", mode: "bm25", page: 1, per_page: 1, filters: {} }).then(function (data) {
          var buckets = (data.facets || {}).source || [];
          var box = body.querySelector("#ingest-sources");
          if (!box) return;
          box.innerHTML = buckets.length
            ? '<table class="detail-table"><tbody>' + buckets.map(function (b) { return row(esc(b.value), String(b.count)); }).join("") + "</tbody></table>"
            : '<p class="muted">No documents indexed yet.</p>';
        }).catch(function (error) {
          var box = body.querySelector("#ingest-sources");
          if (box && !isAbort(error)) box.innerHTML = '<p class="status-err">Counts unavailable: ' + esc(error.message || String(error)) + "</p>";
        });
      });
    function row(k, v) { return '<tr><th scope="row">' + k + "</th><td>" + v + "</td></tr>"; }
  }
  function openIngestHelp() {
    showDialog("How to Ingest",
      "<p>Load documents from the backend Shell (or <code>./run flask …</code>):</p>" +
      '<div class="sunken" style="padding:8px;font-family:var(--font-mono);white-space:pre;user-select:text">' +
      esc("flask engine index-init\nflask engine demo\nflask engine seed-corpus --target 300\nflask engine ingest arxiv -q \"cat:eess.SY\" -n 500\nflask engine ingest github -q \"topic:rtos stars:>1000\" -n 100") +
      "</div>" + okBar());
  }

  /* ----------------------------------------------------- title-bar buttons
     They do what their glyphs promise, within a browser page: "_" folds the
     Workspace Explorer away, "▢" toggles full screen, "✕" closes the current
     search. */
  function initTitleBar() {
    var minimize = document.querySelector('.tb-btn[data-tb="explorer"]');
    var maximize = document.querySelector('.tb-btn[data-tb="fullscreen"]');
    var close = document.querySelector('.tb-btn[data-tb="close"]');
    minimize.addEventListener("click", function () {
      var hidden = document.body.classList.toggle("explorer-hidden");
      var label = hidden ? "Show Workspace Explorer" : "Hide Workspace Explorer";
      minimize.title = label;
      minimize.setAttribute("aria-label", label);
      minimize.setAttribute("aria-pressed", hidden ? "true" : "false");
    });
    var root = document.documentElement;
    if (!document.fullscreenEnabled || !root.requestFullscreen) {
      maximize.hidden = true; // no full screen here: no button that pretends
    } else {
      maximize.addEventListener("click", function () {
        if (document.fullscreenElement) document.exitFullscreen();
        else root.requestFullscreen().catch(function () {});
      });
      document.addEventListener("fullscreenchange", function () {
        var full = !!document.fullscreenElement;
        maximize.title = full ? "Exit full screen" : "Full screen";
        maximize.setAttribute("aria-label", maximize.title);
        maximize.setAttribute("aria-pressed", full ? "true" : "false");
      });
    }
    close.addEventListener("click", newSearch);
  }

  /* -------------------------------------------------------------- init */
  function loadSourcesCatalog() {
    runtime.sources()
      .then(function (data) {
        sourcesCatalog = (data && data.sources) || [];
      })
      .catch(function (error) {
        if (!error || error.name !== "AbortError") sourcesCatalog = [];
      });
  }

  function init() {
    // Toolbar search
    $("#search-form").addEventListener("submit", function (e) {
      e.preventDefault(); state.q = $("#q").value.trim(); state.page = 1; doSearch();
    });
    $("#mode").addEventListener("change", function () { state.mode = $("#mode").value; state.page = 1; if (state.q) doSearch(); });

    // Menu bar
    document.querySelectorAll(".menu").forEach(function (m) {
      m.addEventListener("click", function (e) {
        e.stopPropagation();
        if (openMenu === m) { closeMenu(); } else { showMenu(m); }
      });
      m.addEventListener("mouseenter", function () { if (openMenu && openMenu !== m) showMenu(m); });
      m.addEventListener("keydown", onMenuBarKey);
      m.addEventListener("focus", function () { focusMenuBar(m); });
    });
    $("#menu-popup").addEventListener("keydown", onPopupKey);
    document.addEventListener("click", function () { closeMenu(); });

    initTitleBar();

    // Dialog close affordances
    $("#dialog-x").addEventListener("click", closeDialog);
    $("#dialog-overlay").addEventListener("click", function (e) { if (e.target === $("#dialog-overlay")) closeDialog(); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { closeDialog(); closeMenu(true); }
      trapDialogTab(e);
    });
    // "/" focuses search, the way every search-first tool behaves.
    document.addEventListener("keydown", function (e) {
      var tag = e.target && e.target.tagName;
      if (e.key !== "/" || tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      $("#q").focus();
      $("#q").select();
      e.preventDefault();
    });

    $("#export-markdown").addEventListener("click", function () { exportResearch("markdown"); });
    $("#export-json").addEventListener("click", function () { exportResearch("json"); });
    readState();
    renderTree();
    renderWelcome();
    runtime.subscribe(applyRuntimeSnapshot);
    $("#runtime-action").addEventListener("click", handleRuntimeAction);
    var toolbarBtn = $("#toolbar-live-btn");
    if (toolbarBtn) {
      toolbarBtn.addEventListener("click", function () {
        if (runtimeSnapshot.provider === "live") chooseDemo();
        else chooseLive();
      });
    }
    var badge = $("#runtime-badge");
    if (badge) {
      badge.addEventListener("click", function () {
        if (runtimeSnapshot.provider === "live" && runtimeSnapshot.phase === "live") chooseDemo();
        else handleRuntimeAction();
      });
    }
    // Nothing waits on the probe: a query in the address is queued now and
    // runs the moment Anna is online (sources and collections load then too).
    runtime.start().catch(function () {});
    doSearch();
    window.addEventListener("beforeunload", runtime.stop);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
