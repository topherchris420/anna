/* Vers3Dynamics Engineering Intelligence — Visual Search Studio (Win95 skin).
 * Framework-free SPA talking to the backend REST API (/api/v1). All fetches go
 * through apiUrl(), which resolves window.ENGINE_API_BASE (see config.js) plus
 * the ?api= / localStorage runtime overrides.
 */
(function () {
  "use strict";

  /* ------------------------------------------------------------------ API */
  function normalizeBase(url) {
    if (!url) return "";
    return url.trim().replace(/\/+$/, "").replace(/\/api\/v1$/, "");
  }
  function apiBase() {
    var qp = new URLSearchParams(location.search).get("api");
    if (qp) return normalizeBase(qp);
    var stored = localStorage.getItem("engine_api_base");
    if (stored) return normalizeBase(stored);
    return normalizeBase(window.ENGINE_API_BASE || "");
  }
  function apiUrl(path) { return apiBase() + "/api/v1" + path; }
  function getJSON(path, opts) {
    return fetch(apiUrl(path), opts).then(function (r) {
      return r.json().then(function (b) {
        if (!r.ok && !b) throw new Error("HTTP " + r.status);
        return b;
      });
    });
  }

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

  /* --------------------------------------------------------------- state */
  var MULTI = ["source", "kind", "category", "language"];
  var BOOL = ["has_code", "has_equations"];
  var CATEGORIES = [
    { label: "Academic Papers",   icon: "📄", values: ["paper"] },
    { label: "Technical Reports", icon: "📑", values: ["report"] },
    { label: "Silicon Datasheets",icon: "🔩", values: ["documentation", "datasheet"] },
    { label: "Source Code",       icon: "💾", values: ["repository", "code"] },
    { label: "Standards",         icon: "📐", values: ["standard"] },
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
  var sourcesCatalog = null; // filled from /api/v1/sources

  function readState() {
    var p = new URLSearchParams(location.search);
    state.q = p.get("q") || "";
    state.mode = ["hybrid", "bm25", "semantic"].indexOf(p.get("mode")) >= 0 ? p.get("mode") : "hybrid";
    state.page = parseInt(p.get("page"), 10) || 1;
    state.filters = {};
    MULTI.forEach(function (k) { var v = p.getAll(k); if (v.length) state.filters[k] = v; });
    BOOL.forEach(function (k) { if (p.get(k) === "true") state.filters[k] = "true"; });
  }
  function buildQuery() {
    var p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    p.set("mode", state.mode);
    if (state.page > 1) p.set("page", String(state.page));
    p.set("per_page", String(state.per_page));
    MULTI.forEach(function (k) { (state.filters[k] || []).forEach(function (v) { p.append(k, v); }); });
    BOOL.forEach(function (k) { if (state.filters[k] === "true") p.set(k, "true"); });
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
  function newSearch() { state.q = ""; state.filters = {}; state.page = 1; $("#q").value = ""; doSearch(); $("#q").focus(); }

  /* -------------------------------------------------------------- search */
  function doSearch() {
    syncUrl();
    $("#q").value = state.q;
    $("#mode").value = state.mode;

    if (!state.q) {
      lastFacets = {};
      renderTree();
      renderWelcome();
      $("#summary").hidden = true;
      $("#pager").innerHTML = "";
      $("#pane-count").textContent = "";
      setMetrics("Ready");
      return;
    }

    setMetrics("Searching…");
    $("#results").innerHTML = '<div class="result-window"><div class="rw-title"><span>Working</span></div>' +
      '<div class="rw-body spinner-text">Executing hybrid retrieval…</div></div>';
    $("#summary").hidden = true;
    $("#pager").innerHTML = "";

    getJSON("/search?" + buildQuery().toString())
      .then(function (data) {
        if (data.error) return renderError(data.error);
        lastFacets = data.facets || {};
        renderTree();
        renderResults(data);
        renderPager(data);
        setMetrics(data.total + " result" + (data.total === 1 ? "" : "s") + " · " +
          (data.took_ms || 0) + "ms · " + state.mode);
        $("#pane-count").textContent = "(" + data.total + ")";
        loadSummary();
      })
      .catch(function (e) { renderError(e.message || String(e)); });
  }

  /* ---------------------------------------------------------- tree view */
  function treeNode(icon, label, active, count, onClick) {
    var node = el("div", "tree-node" + (active ? " active" : ""));
    node.innerHTML =
      '<span class="tn-icon">' + icon + "</span>" +
      "<span>" + esc(label) + "</span>" +
      (count != null ? '<span class="tn-count">' + count + "</span>" : "");
    node.addEventListener("click", onClick);
    return node;
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
    var g2 = el("div", "tree-group");
    g2.appendChild(el("div", "tree-root", "🗀 Sources"));
    var srcBuckets = lastFacets.source || [];
    if (srcBuckets.length) {
      var active = state.filters.source || [];
      srcBuckets.forEach(function (b) {
        var v = String(b.value);
        g2.appendChild(treeNode("🔌", v, active.indexOf(v) >= 0, b.count,
          function () { toggleMulti("source", v); }));
      });
    } else {
      g2.appendChild(el("div", "tree-empty", "— run a search —"));
    }
    tree.appendChild(g2);

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
  }

  /* ------------------------------------------------------------- results */
  function resultWindow(titleLeft, kind, flagsHtml, bodyHtml) {
    return '<div class="result-window">' +
      '<div class="rw-title"><span>' + titleLeft + "</span>" +
      (flagsHtml || "") +
      (kind ? '<span class="rw-kind">' + esc(kind) + "</span>" : "") +
      "</div><div class=\"rw-body\">" + bodyHtml + "</div></div>";
  }

  function renderResults(data) {
    if (!data.hits || !data.hits.length) {
      $("#results").innerHTML = resultWindow("No Results", "", "",
        '<div class="rw-heading">Nothing found for “' + esc(state.q) + '”.</div>' +
        '<p>Try a broader query, clear filters, or switch to ' +
        '<a class="link" id="try-semantic">Semantic</a> mode.</p>');
      var t = $("#try-semantic");
      if (t) t.addEventListener("click", function () { $("#mode").value = "semantic"; state.mode = "semantic"; state.page = 1; doSearch(); });
      return;
    }
    var html = data.hits.map(function (hit) {
      var d = hit.document;
      var snippet = (hit.highlights && hit.highlights.length)
        ? highlight(hit.highlights[0])
        : esc((d.abstract || "").slice(0, 300)) + ((d.abstract || "").length > 300 ? "…" : "");
      var authors = (d.authors || []).slice(0, 4).join(", ") + ((d.authors || []).length > 4 ? " et al." : "");
      var meta = esc(authors) + (d.published ? (authors ? " · " : "") + esc(String(d.published).slice(0, 10)) : "");
      var tags = (d.categories || []).slice(0, 4).concat((d.tags || []).slice(0, 4))
        .map(function (x) { return '<span class="rw-tag">' + esc(x) + "</span>"; }).join("");
      var flags = (d.version ? '<span class="rw-flag">v:' + esc(d.version) + "</span>" : "") +
        (d.has_equations ? '<span class="rw-flag">∑</span>' : "") +
        (d.has_code ? '<span class="rw-flag">≡</span>' : "");
      var actions = [];
      if (d.url) actions.push('<a class="link" href="' + esc(d.url) + '" target="_blank" rel="noopener">Open source ↗</a>');
      if (d.pdf_url) actions.push('<a class="link" href="' + esc(d.pdf_url) + '" target="_blank" rel="noopener">PDF</a>');

      var body =
        '<div class="rw-heading">' +
          (d.url ? '<a class="link" href="' + esc(d.url) + '" target="_blank" rel="noopener">' + esc(d.title) + "</a>" : esc(d.title)) +
        "</div>" +
        '<div class="rw-meta">' + (meta || "&nbsp;") +
          ' <span class="rw-score">· rrf ' + (hit.score != null ? Number(hit.score).toFixed(4) : "") + "</span></div>" +
        '<div class="rw-snippet">' + snippet + "</div>" +
        (tags ? '<div class="rw-tags">' + tags + "</div>" : "") +
        (actions.length ? '<div class="rw-actions">' + actions.join("") + "</div>" : "");
      return resultWindow(esc(d.source), d.kind, flags, body);
    }).join("");
    $("#results").innerHTML = html;
  }

  function renderWelcome() {
    var ex = EXAMPLES.map(function (q) { return '<span class="example" data-ex="' + esc(q) + '">' + esc(q) + "</span>"; }).join("");
    $("#results").innerHTML = resultWindow("Getting Started", "readme", "",
      '<div class="welcome-body">' +
        "<h2>Vers3Dynamics OmniLogic Workbench</h2>" +
        "<p>Hybrid semantic + lexical search over research papers, standards, source code, " +
        "and vendor documentation, with citation-first answers. Type a query in the toolbar, " +
        "or filter categories in the Workspace Explorer.</p>" +
        '<div class="rw-heading">Example queries</div>' +
        '<div class="example-list">' + ex + "</div>" +
      "</div>");
    $("#results").querySelectorAll("[data-ex]").forEach(function (n) {
      n.addEventListener("click", function () { state.q = n.dataset.ex; state.page = 1; state.filters = {}; doSearch(); });
    });
  }

  function renderError(msg) {
    lastFacets = {}; renderTree();
    $("#results").innerHTML = resultWindow("Connection Error", "error", "",
      '<div class="rw-heading">Could not reach the API.</div>' +
      "<p>" + esc(msg) + "</p>" +
      "<p>Set the backend endpoint via <b>Edit ▸ API Endpoint…</b>, then search again.</p>");
    $("#pager").innerHTML = "";
    setMetrics("Error");
    setConn(false, "Disconnected");
  }

  function renderPager(data) {
    var pages = Math.max(1, Math.ceil((data.total || 0) / state.per_page));
    var box = $("#pager");
    box.innerHTML = "";
    if (data.total <= state.per_page) return;
    if (state.page > 1) {
      var prev = el("button", "btn", "◄ Prev");
      prev.addEventListener("click", function () { state.page--; doSearch(); $("#content-scroll").scrollTop = 0; });
      box.appendChild(prev);
    }
    box.appendChild(el("span", "pg-info", "Page " + state.page + " of " + pages));
    if (state.page < pages && data.hits.length) {
      var next = el("button", "btn", "Next ►");
      next.addEventListener("click", function () { state.page++; doSearch(); $("#content-scroll").scrollTop = 0; });
      box.appendChild(next);
    }
  }

  function loadSummary() {
    var box = $("#summary");
    box.hidden = false;
    box.innerHTML = '<div class="rw-title"><span>AI Answer — Citation Report</span></div>' +
      '<div class="rw-body spinner-text">Synthesizing a citation-first answer…</div>';
    getJSON("/summarize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q: state.q }),
    }).then(function (data) {
      if (!data || data.error) { box.hidden = true; return; }
      var ans = esc(data.answer || "").replace(/\[(\d+)\]/g, "<sup>[$1]</sup>");
      var cites = (data.citations || []).map(function (c) {
        return '<div class="ans-cite"><sup>[' + c.n + "]</sup> " +
          (c.url ? '<a class="link" href="' + esc(c.url) + '" target="_blank" rel="noopener">' + esc(c.title) + "</a>" : esc(c.title)) +
          ' <span class="rw-tag">' + esc(c.source) + "</span></div>";
      }).join("");
      box.innerHTML = '<div class="rw-title"><span>AI Answer — Citation Report</span>' +
        '<span class="rw-kind">' + esc(data.generator || "extractive") + "</span></div>" +
        '<div class="rw-body"><div class="ans-text">' + (ans || "No grounded answer available.") + "</div>" +
        (cites ? '<div class="ans-cites">' + cites + "</div>" : "") + "</div>";
    }).catch(function () { box.hidden = true; });
  }

  /* -------------------------------------------------------- status bar */
  function setConn(ok, text) {
    $("#conn-led").className = "led " + (ok ? "led-green" : "led-red");
    $("#conn-text").textContent = text;
  }
  function setMetrics(text) { $("#status-metrics").textContent = text; }

  function refreshHealth() {
    setConn(false, "Connecting…");
    $("#conn-led").className = "led led-yellow";
    getJSON("/health").then(function (h) {
      var n = h.document_count || 0;
      setConn(!!h.index_exists, "Connected — " + n + " document" + (n === 1 ? "" : "s"));
      $("#worker-led").className = "led " + (h.index_exists ? "led-green" : "led-gray");
      var emb = /MiniLM|sentence/i.test(h.embedding_model || "") ? "" : "";
      $("#engine-text").textContent = "Engine: " + (h.backend || "?") +
        (h.backend === "postgres" ? " (FTS+pgvector)" : "");
    }).catch(function () {
      setConn(false, "Disconnected — check Edit ▸ API Endpoint…");
      $("#worker-led").className = "led led-red";
      $("#engine-text").textContent = "Engine: offline";
    });
  }

  /* ------------------------------------------------------------- menus */
  var openMenu = null;
  function menuDefs() {
    return {
      file: [
        { label: "New Search", act: newSearch },
        { sep: true },
        { label: "Print Results…", act: function () { window.print(); } },
        { sep: true },
        { label: "Exit", act: function () { showDialog("Exit", "<p>Close the browser tab to exit Visual Search Studio.</p>" + okBar()); } },
      ],
      edit: [
        { label: "Clear Filters", act: clearFilters },
        { sep: true },
        { label: "API Endpoint…", act: openApiDialog },
      ],
      ingestion: [
        { label: "Ingestion Status…", act: openIngestionDialog },
        { label: "How to Ingest…", act: openIngestHelp },
      ],
      sources: sourceMenu(),
      help: [
        { label: "REST API (/api/v1/health)", act: function () { window.open(apiUrl("/health"), "_blank"); } },
        { sep: true },
        { label: "About Visual Search Studio…", act: openAbout },
      ],
    };
  }
  function sourceMenu() {
    var items = [{ label: "All Sources (clear)", act: function () { delete state.filters.source; state.page = 1; doSearch(); } }, { sep: true }];
    if (!sourcesCatalog) {
      items.push({ label: "Loading…", disabled: true });
    } else if (!sourcesCatalog.length) {
      items.push({ label: "Unavailable (API offline)", disabled: true });
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

  function closeMenu() {
    var pop = $("#menu-popup"); pop.hidden = true; pop.innerHTML = "";
    if (openMenu) openMenu.classList.remove("open");
    openMenu = null;
  }
  function showMenu(menuEl) {
    var defs = menuDefs()[menuEl.dataset.menu] || [];
    var pop = $("#menu-popup");
    pop.innerHTML = "";
    defs.forEach(function (item) {
      if (item.sep) { pop.appendChild(el("div", "menu-sep")); return; }
      var mi = el("div", "menu-item" + (item.disabled ? " disabled" : ""),
        esc(item.label) + (item.count ? '<span class="mi-count">' + esc(item.count) + "</span>" : ""));
      if (!item.disabled) {
        mi.addEventListener("click", function () { closeMenu(); item.act(); });
      }
      pop.appendChild(mi);
    });
    var r = menuEl.getBoundingClientRect();
    pop.style.left = r.left + "px";
    pop.style.top = r.bottom + "px";
    pop.hidden = false;
    if (openMenu) openMenu.classList.remove("open");
    openMenu = menuEl; menuEl.classList.add("open");
  }

  /* ----------------------------------------------------------- dialogs */
  function okBar() { return '<div class="dialog-actions"><button class="btn btn-default" data-close>OK</button></div>'; }
  function showDialog(title, bodyHtml, onMount) {
    $("#dialog-title").textContent = title;
    $("#dialog-body").innerHTML = bodyHtml;
    $("#dialog-overlay").hidden = false;
    $("#dialog-body").querySelectorAll("[data-close]").forEach(function (b) {
      b.addEventListener("click", closeDialog);
    });
    if (onMount) onMount($("#dialog-body"));
  }
  function closeDialog() { $("#dialog-overlay").hidden = true; $("#dialog-body").innerHTML = ""; }

  function openAbout() {
    showDialog("About Visual Search Studio",
      '<div style="display:flex;gap:12px">' +
        '<div style="font-size:32px">⚙</div><div>' +
        "<div class=\"rw-heading\">Vers3Dynamics Engineering Intelligence</div>" +
        "<p>Visual Search Studio — a Windows&nbsp;95 / Visual Studio 6.0 workspace for " +
        "AI-powered engineering knowledge search.</p>" +
        "<p>Hybrid retrieval (BM25 + vectors), citation-first answers. Open source.</p>" +
        '<p style="color:#404040">Backend: <code>' + esc(apiBase() || "(unset)") + "</code></p>" +
        "</div></div>" + okBar());
  }
  function openApiDialog() {
    var current = localStorage.getItem("engine_api_base") || window.ENGINE_API_BASE || "";
    showDialog("API Endpoint",
      "<p>Backend base URL (scheme + host, no <code>/api/v1</code>):</p>" +
      '<div class="dialog-row"><input class="field" id="api-input" type="url" value="' + esc(current) + '"></div>' +
      '<div class="dialog-row"><button class="btn" id="api-test">Test</button><span id="api-status"></span></div>' +
      '<div class="dialog-actions"><button class="btn btn-default" id="api-ok">OK</button>' +
      '<button class="btn" data-close>Cancel</button></div>',
      function () {
        $("#api-test").addEventListener("click", function () {
          var st = $("#api-status"); st.textContent = "Testing…"; st.className = "";
          var base = normalizeBase($("#api-input").value);
          fetch(base + "/api/v1/health").then(function (r) { return r.json(); }).then(function (h) {
            st.textContent = "✓ Connected · " + (h.document_count || 0) + " docs"; st.className = "status-ok";
          }).catch(function () { st.textContent = "✗ Unreachable"; st.className = "status-err"; });
        });
        $("#api-ok").addEventListener("click", function () {
          localStorage.setItem("engine_api_base", normalizeBase($("#api-input").value));
          closeDialog(); refreshHealth(); doSearch();
        });
      });
  }
  function openIngestionDialog() {
    showDialog("Ingestion Status", '<p class="spinner-text">Querying engine…</p>' + okBar(), function (body) {
      getJSON("/health").then(function (h) {
        body.innerHTML =
          '<table style="border-collapse:collapse">' +
          row("Backend", esc(h.backend || "?")) +
          row("Index", esc(h.index || "?") + (h.index_exists ? " (ready)" : " (missing)")) +
          row("Documents", String(h.document_count || 0)) +
          row("Embeddings", esc(h.embedding_model || "?")) +
          row("Status", '<span class="' + (h.backend_status === "ok" ? "status-ok" : "status-err") + '">' + esc(h.backend_status || "?") + "</span>") +
          "</table>" + okBar();
        body.querySelectorAll("[data-close]").forEach(function (b) { b.addEventListener("click", closeDialog); });
      }).catch(function () {
        body.innerHTML = '<p class="status-err">Engine unreachable.</p>' + okBar();
        body.querySelectorAll("[data-close]").forEach(function (b) { b.addEventListener("click", closeDialog); });
      });
    });
    function row(k, v) { return '<tr><td style="padding:2px 12px 2px 0;color:#404040">' + k + "</td><td style=\"padding:2px 0\">" + v + "</td></tr>"; }
  }
  function openIngestHelp() {
    showDialog("How to Ingest",
      "<p>Load documents from the backend Shell (or <code>./run flask …</code>):</p>" +
      '<div class="sunken" style="padding:8px;font-family:var(--font-mono);white-space:pre;user-select:text">' +
      esc("flask engine index-init\nflask engine demo\nflask engine ingest arxiv -q \"cat:eess.SY\" -n 500\nflask engine ingest github -q \"topic:rtos stars:>1000\" -n 100") +
      "</div>" + okBar());
  }

  /* -------------------------------------------------------------- init */
  function loadSourcesCatalog() {
    getJSON("/sources").then(function (d) { sourcesCatalog = (d && d.sources) || []; })
      .catch(function () { sourcesCatalog = []; });
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
    });
    document.addEventListener("click", closeMenu);

    // Title-bar close = clear (visual)
    document.querySelector(".tb-close").addEventListener("click", newSearch);

    // Dialog close affordances
    $("#dialog-x").addEventListener("click", closeDialog);
    $("#dialog-overlay").addEventListener("click", function (e) { if (e.target === $("#dialog-overlay")) closeDialog(); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { closeDialog(); closeMenu(); }
    });

    loadSourcesCatalog();
    refreshHealth();
    readState();
    doSearch();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
