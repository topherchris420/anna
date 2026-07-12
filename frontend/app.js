/* Vers3Dynamics Engineering Intelligence — static frontend.
 * Framework-free SPA that talks to the backend REST API (/api/v1).
 * Deployable to any static host (Vercel, Dappling Network, Netlify, ...).
 */
(function () {
  "use strict";

  // ---------------------------------------------------------------- API base
  function normalizeBase(url) {
    if (!url) return "";
    url = url.trim().replace(/\/+$/, "");
    return url.replace(/\/api\/v1$/, ""); // tolerate a pasted full API path
  }
  function apiBase() {
    var qp = new URLSearchParams(location.search).get("api");
    if (qp) return normalizeBase(qp);
    var stored = localStorage.getItem("engine_api_base");
    if (stored) return normalizeBase(stored);
    return normalizeBase(window.ENGINE_API_BASE || "");
  }
  function apiUrl(path) {
    return apiBase() + "/api/v1" + path;
  }

  function getJSON(path, opts) {
    return fetch(apiUrl(path), opts).then(function (r) {
      return r.json().then(function (body) {
        if (!r.ok && !body) throw new Error("HTTP " + r.status);
        return body;
      });
    });
  }

  // ------------------------------------------------------------------ helpers
  function $(sel) { return document.querySelector(sel); }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  // Escape everything, then re-allow only <em>…</em> from ES highlights.
  function highlight(s) {
    return esc(s).replace(/&lt;em&gt;/g, "<em>").replace(/&lt;\/em&gt;/g, "</em>");
  }

  var MULTI = ["source", "kind", "category", "language"];
  var BOOL = ["has_code", "has_equations"];
  var FACET_LABEL = {
    source: "Source", kind: "Type", categories: "Category",
    language: "Language", has_code: "Has code", has_equations: "Has equations"
  };
  var GROUP_PARAM = { source: "source", kind: "kind", categories: "category", language: "language" };

  // -------------------------------------------------------------------- state
  var state = { q: "", mode: "hybrid", page: 1, per_page: 20, filters: {} };

  function readStateFromUrl() {
    var p = new URLSearchParams(location.search);
    state.q = p.get("q") || "";
    state.mode = ["hybrid", "bm25", "semantic"].indexOf(p.get("mode")) >= 0 ? p.get("mode") : "hybrid";
    state.page = parseInt(p.get("page"), 10) || 1;
    state.filters = {};
    MULTI.forEach(function (k) {
      var vals = p.getAll(k);
      if (vals.length) state.filters[k] = vals;
    });
    BOOL.forEach(function (k) { if (p.get(k) === "true") state.filters[k] = "true"; });
  }

  function buildQuery() {
    var p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    p.set("mode", state.mode);
    if (state.page > 1) p.set("page", String(state.page));
    p.set("per_page", String(state.per_page));
    MULTI.forEach(function (k) {
      (state.filters[k] || []).forEach(function (v) { p.append(k, v); });
    });
    BOOL.forEach(function (k) { if (state.filters[k] === "true") p.set(k, "true"); });
    return p;
  }

  function syncUrl() {
    var p = buildQuery();
    p.delete("per_page");
    var api = new URLSearchParams(location.search).get("api");
    if (api) p.set("api", api);
    history.replaceState(null, "", "?" + p.toString());
  }

  // ------------------------------------------------------------------- toggles
  function toggleMulti(param, value) {
    var arr = state.filters[param] || [];
    if (arr.indexOf(value) >= 0) arr = arr.filter(function (v) { return v !== value; });
    else arr = arr.concat([value]);
    if (arr.length) state.filters[param] = arr; else delete state.filters[param];
    state.page = 1;
    doSearch();
  }
  function toggleBool(param) {
    if (state.filters[param] === "true") delete state.filters[param];
    else state.filters[param] = "true";
    state.page = 1;
    doSearch();
  }
  function setMode(mode) { state.mode = mode; state.page = 1; doSearch(); }
  function gotoPage(n) { state.page = n; doSearch(); window.scrollTo(0, 0); }

  // -------------------------------------------------------------------- search
  function doSearch() {
    if (!state.q) { showLanding(); return; }
    syncUrl();
    $("#landing").hidden = true;
    $("#results-view").hidden = false;
    $("#q").value = state.q;
    document.querySelectorAll(".modes button").forEach(function (b) {
      b.classList.toggle("on", b.dataset.mode === state.mode);
    });
    $("#result-meta").innerHTML = '<span class="spinner"></span> searching…';
    $("#results").innerHTML = "";
    $("#summary").hidden = true;

    getJSON("/search?" + buildQuery().toString())
      .then(function (data) {
        if (data.error) return renderError(data.error);
        renderMeta(data);
        renderFacets(data);
        renderResults(data);
        renderPager(data);
        loadSummary();
      })
      .catch(function (e) { renderError(e.message || String(e)); });
  }

  function renderError(msg) {
    $("#result-meta").textContent = "";
    $("#sidebar").innerHTML = "";
    $("#results").innerHTML =
      '<div class="panel empty"><p style="font-size:17px">Could not reach the API.</p>' +
      '<p class="muted">' + esc(msg) + "</p>" +
      '<p>Set your backend endpoint with the ⚙︎ button (top-right), then search again.</p></div>';
    $("#pager").innerHTML = "";
  }

  function renderMeta(data) {
    $("#result-meta").textContent = data.total + " results · " + (data.took_ms || 0) + "ms";
  }

  function renderResults(data) {
    if (!data.hits || !data.hits.length) {
      $("#results").innerHTML =
        '<div class="panel empty"><p style="font-size:17px">No results for “' +
        esc(state.q) + '”.</p><p>Try a broader query or switch to ' +
        '<a href="#" id="try-semantic">semantic mode</a>.</p></div>';
      var t = $("#try-semantic");
      if (t) t.addEventListener("click", function (e) { e.preventDefault(); setMode("semantic"); });
      return;
    }
    $("#results").innerHTML = data.hits.map(function (hit) {
      var d = hit.document;
      var snippet = (hit.highlights && hit.highlights.length)
        ? highlight(hit.highlights[0])
        : esc((d.abstract || "").slice(0, 280)) + ((d.abstract || "").length > 280 ? "…" : "");
      var authors = (d.authors || []).slice(0, 4).join(", ") +
        ((d.authors || []).length > 4 ? " et al." : "");
      var tags = (d.categories || []).slice(0, 5).map(function (c) {
        return '<span class="tag">' + esc(c) + "</span>";
      }).concat((d.tags || []).slice(0, 5).map(function (t) {
        return '<span class="tag">#' + esc(t) + "</span>";
      })).join("");
      return '' +
        '<article class="result panel">' +
          '<div class="rtop">' +
            '<span class="src-badge">' + esc(d.source) + "</span>" +
            '<span class="tag">' + esc(d.kind) + "</span>" +
            (d.version ? '<span class="tag">v: ' + esc(d.version) + "</span>" : "") +
            (d.has_equations ? '<span class="tag">∑ equations</span>' : "") +
            (d.has_code ? '<span class="tag">{ } code</span>' : "") +
            '<span class="score">rrf ' + (hit.score != null ? Number(hit.score).toFixed(4) : "") + "</span>" +
          "</div>" +
          "<h3>" + (d.url ? '<a href="' + esc(d.url) + '" target="_blank" rel="noopener">' + esc(d.title) + "</a>" : esc(d.title)) + "</h3>" +
          '<div class="meta">' + esc(authors) + (d.published ? " · " + esc(String(d.published).slice(0, 10)) : "") + "</div>" +
          '<div class="snip">' + snippet + "</div>" +
          (tags ? '<div class="rtags">' + tags + "</div>" : "") +
          '<div class="actions">' +
            (d.url ? '<a href="' + esc(d.url) + '" target="_blank" rel="noopener">Open source ↗</a>' : "") +
            (d.pdf_url ? '<a href="' + esc(d.pdf_url) + '" target="_blank" rel="noopener">PDF</a>' : "") +
          "</div>" +
        "</article>";
    }).join("");
  }

  function renderFacets(data) {
    var facets = data.facets || {};
    var html = "";

    // Active filters
    var active = [];
    MULTI.forEach(function (k) {
      (state.filters[k] || []).forEach(function (v) {
        active.push('<span class="chip on" data-toggle="' + k + '" data-value="' + esc(v) + '">' + esc(v) + " ✕</span>");
      });
    });
    BOOL.forEach(function (k) {
      if (state.filters[k] === "true")
        active.push('<span class="chip on" data-boolfilter="' + k + '">' + FACET_LABEL[k] + " ✕</span>");
    });
    if (active.length) {
      html += '<div class="facet panel"><h4>Active filters</h4><div class="active-filters">' + active.join("") + "</div></div>";
    }

    ["source", "kind", "categories", "language"].forEach(function (group) {
      var buckets = facets[group] || [];
      if (!buckets.length) return;
      var param = GROUP_PARAM[group];
      var selected = state.filters[param] || [];
      html += '<div class="facet panel"><h4>' + FACET_LABEL[group] + "</h4>";
      buckets.forEach(function (b) {
        var on = selected.indexOf(String(b.value)) >= 0;
        html += '<div class="opt' + (on ? " active" : "") + '" data-toggle="' + param + '" data-value="' + esc(b.value) + '">' +
          '<span class="lbl"><span class="box"></span>' + esc(b.value) + "</span>" +
          '<span class="cnt">' + b.count + "</span></div>";
      });
      html += "</div>";
    });

    BOOL.forEach(function (param) {
      var buckets = facets[param] || [];
      var trueBucket = buckets.filter(function (b) { return b.value === true || b.value === 1 || b.value === "true"; })[0];
      var count = trueBucket ? trueBucket.count : 0;
      if (!count && state.filters[param] !== "true") return;
      var on = state.filters[param] === "true";
      html += '<div class="facet panel"><h4>' + FACET_LABEL[param] + "</h4>" +
        '<div class="opt' + (on ? " active" : "") + '" data-boolfilter="' + param + '">' +
        '<span class="lbl"><span class="box"></span>' + FACET_LABEL[param] + "</span>" +
        '<span class="cnt">' + count + "</span></div></div>";
    });

    $("#sidebar").innerHTML = html;
    $("#sidebar").querySelectorAll("[data-toggle]").forEach(function (elm) {
      elm.addEventListener("click", function () { toggleMulti(elm.dataset.toggle, elm.dataset.value); });
    });
    $("#sidebar").querySelectorAll("[data-boolfilter]").forEach(function (elm) {
      elm.addEventListener("click", function () { toggleBool(elm.dataset.boolfilter); });
    });
  }

  function renderPager(data) {
    var totalPages = Math.max(1, Math.ceil((data.total || 0) / state.per_page));
    var html = "";
    if (state.page > 1) html += '<button class="btn" data-page="' + (state.page - 1) + '">← Prev</button>';
    html += '<span class="btn" style="cursor:default">Page ' + state.page + " / " + totalPages + "</span>";
    if (state.page < totalPages && data.hits && data.hits.length)
      html += '<button class="btn" data-page="' + (state.page + 1) + '">Next →</button>';
    $("#pager").innerHTML = html;
    $("#pager").querySelectorAll("[data-page]").forEach(function (b) {
      b.addEventListener("click", function () { gotoPage(parseInt(b.dataset.page, 10)); });
    });
  }

  function loadSummary() {
    var box = $("#summary");
    box.hidden = false;
    box.innerHTML = '<h3>⚙ AI answer</h3><div class="ans muted"><span class="spinner"></span> synthesizing a citation-first answer…</div>';
    getJSON("/summarize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q: state.q })
    }).then(function (data) {
      if (!data || data.error) { box.hidden = true; return; }
      var ans = esc(data.answer || "").replace(/\[(\d+)\]/g, "<sup>[$1]</sup>");
      var cites = (data.citations || []).map(function (c) {
        return '<div class="c"><sup>[' + c.n + "]</sup> " +
          (c.url ? '<a href="' + esc(c.url) + '" target="_blank" rel="noopener">' + esc(c.title) + "</a>" : esc(c.title)) +
          ' <span class="tag">' + esc(c.source) + "</span></div>";
      }).join("");
      box.innerHTML = '<h3>⚙ AI answer <span class="muted" style="text-transform:none;font-weight:400">· ' +
        esc(data.generator || "extractive") + "</span></h3>" +
        '<div class="ans">' + (ans || "No grounded answer available.") + "</div>" +
        (cites ? '<div class="cites">' + cites + "</div>" : "");
    }).catch(function () { box.hidden = true; });
  }

  // ------------------------------------------------------------------ landing
  var EXAMPLES = [
    "STM32 DMA circular buffer",
    "reinforcement learning for control systems",
    "RISC-V vector extension",
    "ESP32 deep sleep power consumption",
    "finite element stress analysis"
  ];
  function showLanding() {
    $("#results-view").hidden = true;
    $("#landing").hidden = false;
  }
  function renderExamples() {
    $("#examples").innerHTML = EXAMPLES.map(function (ex) {
      return '<span class="chip" data-ex="' + esc(ex) + '">' + esc(ex) + "</span>";
    }).join("");
    $("#examples").querySelectorAll("[data-ex]").forEach(function (c) {
      c.addEventListener("click", function () {
        state.q = c.dataset.ex; state.page = 1; state.filters = {}; doSearch();
      });
    });
  }

  // ------------------------------------------------------------------ settings
  function initSettings() {
    var base = apiBase();
    $("#api-echo").textContent = base || "(unset)";
    $("#api-base").value = localStorage.getItem("engine_api_base") || window.ENGINE_API_BASE || "";
    $("#settings-btn").addEventListener("click", function () {
      var s = $("#settings"); s.hidden = !s.hidden;
    });
    $("#api-save").addEventListener("click", function () {
      var val = normalizeBase($("#api-base").value);
      localStorage.setItem("engine_api_base", val);
      $("#api-echo").textContent = val || "(unset)";
      testConnection();
    });
  }
  function testConnection() {
    var st = $("#api-status");
    st.textContent = "checking…"; st.className = "apistatus";
    getJSON("/health").then(function (h) {
      st.textContent = "✓ connected · " + (h.document_count || 0) + " docs";
      st.className = "apistatus ok";
      if (state.q) doSearch();
    }).catch(function () {
      st.textContent = "✗ unreachable"; st.className = "apistatus err";
    });
  }

  // ---------------------------------------------------------------------- init
  function init() {
    renderExamples();
    initSettings();
    $("#search-form").addEventListener("submit", function (e) {
      e.preventDefault();
      state.q = $("#q").value.trim();
      state.page = 1;
      doSearch();
    });
    window.addEventListener("popstate", function () { readStateFromUrl(); doSearch(); });
    readStateFromUrl();
    if (state.q) doSearch(); else showLanding();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
