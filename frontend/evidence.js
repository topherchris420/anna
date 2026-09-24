/* Source excerpts and portable research records. No services or dependencies. */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.EngineEvidence = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  var STOP = new Set(("a an and are as at be been by can could do does for from has have how " +
    "i in is it its of on or should that the their these this to was were " +
    "what when where which who why will with would you your").split(" "));
  function terms(text) {
    return Array.from(new Set((String(text || "").toLowerCase().match(/[\p{L}\p{N}]+/gu) || [])
      .filter(function (term) { return !STOP.has(term); })));
  }
  function safeUrl(value) {
    try {
      var url = new URL(String(value || ""));
      return /^(https?:)$/.test(url.protocol) && !url.username && !url.password ? url.href : "";
    } catch (_) { return ""; }
  }
  function select(query, documents, limit) {
    var queryTerms = terms(query);
    var candidates = [];
    documents.forEach(function (doc, index) {
      ["abstract", "body"].forEach(function (field) {
        var text = String(doc[field] || "").slice(0, 24000);
        var pattern = /\S[^\n]*?(?:[.!?](?=\s|$)|$|(?=\n))/g;
        var match;
        while ((match = pattern.exec(text)) !== null) {
          var quote = match[0].trimEnd();
          if (quote.length <= 20 || quote.length > 1000) continue;
          var words = terms(quote);
          var matched = queryTerms.filter(function (t) { return words.indexOf(t) >= 0; }).sort();
          if (!matched.length) continue;
          candidates.push({ index: index, doc: doc,
            score: matched.length / Math.sqrt((quote.match(/[\p{L}\p{N}]+/gu) || []).length || 1),
            excerpt: { document_id: doc.id, field: field, start: match.index,
              end: match.index + quote.length, offset_unit: "utf-16", quote: quote, matched_terms: matched } });
        }
      });
    });
    candidates.sort(function (a, b) { return b.score - a.score || a.index - b.index ||
      a.excerpt.field.localeCompare(b.excerpt.field) || a.excerpt.start - b.excerpt.start; });
    var seen = new Set(), counts = new Map(), chosen = [];
    [1, 2].forEach(function (perSource) {
      candidates.forEach(function (item) {
        var key = item.excerpt.quote.toLowerCase().replace(/\s+/g, " ");
        if (chosen.length >= (limit || 5) || seen.has(key) || (counts.get(item.doc.id) || 0) >= perSource) return;
        seen.add(key); counts.set(item.doc.id, (counts.get(item.doc.id) || 0) + 1); chosen.push(item);
      });
    });
    return chosen;
  }
  function summarize(query, documents) {
    var chosen = select(query, documents, 5);
    var citations = [], byId = new Map();
    var answer = chosen.map(function (item) {
      var doc = item.doc;
      if (!byId.has(doc.id)) {
        var citation = { n: citations.length + 1, id: doc.id, title: doc.title,
          url: safeUrl(doc.url || doc.pdf_url), source: doc.source, excerpts: [] };
        citations.push(citation); byId.set(doc.id, citation);
      }
      var cite = byId.get(doc.id); cite.excerpts.push(item.excerpt);
      return item.excerpt.quote.replace(/\[(\d+)\]/g, "［$1］") + " [" + cite.n + "]";
    }).join(" ");
    return { query: query, answer: answer || "The bundled demo sources do not answer this query. Switch to Live Mode to search the full index.",
      generator: "demo-extractive", grounding: chosen.length ? "source-extract" : "insufficient-evidence",
      fallback_reason: null, citations: citations };
  }
  function canonical(value) {
    if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
    if (value && typeof value === "object") return "{" + Object.keys(value).sort().map(function (k) {
      return JSON.stringify(k) + ":" + canonical(value[k]);
    }).join(",") + "}";
    return JSON.stringify(value);
  }
  function createRecord(request, results, summary, provider) {
    // Allowlist the request: never export an API endpoint, credentials, or localStorage.
    var record = {
      schema: "anna-research-record/v1",
      provider: provider,
      request: { q: request.q, mode: request.mode, page: request.page,
        per_page: request.per_page, filters: request.filters || {} },
      retrieval: results.retrieval || null,
      result_count: results.total,
      scope: "current-page",
      hits: (results.hits || []).map(function (hit) {
        var doc = hit.document;
        return { score: hit.score, explanation: hit.explanation || null,
          document: { id: doc.id, title: doc.title, source: doc.source,
            url: safeUrl(doc.url), pdf_url: safeUrl(doc.pdf_url), authors: doc.authors || [],
            published: doc.published || "", version: doc.version || "", abstract: doc.abstract || "" },
          highlights: hit.highlights || [] };
      }),
      summary: summary || null,
    };
    // Snapshot now, so a later query or summary cannot alter an in-flight export.
    return JSON.parse(JSON.stringify(record));
  }
  async function fingerprint(record, cryptoApi) {
    var api = cryptoApi || globalThis.crypto;
    if (!api || !api.subtle) throw new Error("SHA-256 export requires HTTPS or localhost.");
    var digest = await api.subtle.digest("SHA-256", new TextEncoder().encode(canonical(record)));
    return Array.from(new Uint8Array(digest), function (b) { return b.toString(16).padStart(2, "0"); }).join("");
  }
  async function packet(record, cryptoApi) {
    var snapshot = JSON.parse(JSON.stringify(record));
    return { captured_at: new Date().toISOString(), content_sha256: await fingerprint(snapshot, cryptoApi), record: snapshot };
  }
  function markdownText(value) {
    return String(value == null ? "" : value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/([\\`*_{}\[\]()#+.!|~-])/g, "\\$1");
  }
  function markdown(record) {
    var lines = ["# Anna research record", "", "Query: " + markdownText(record.request.q), "",
      "Provider: " + markdownText(record.provider) + " · Scope: current result page", "",
      "Retrieval: " + markdownText((record.retrieval || {}).executed || "Not reported"), "",
      "## Source report", "", markdownText(record.summary ? record.summary.answer : "Summary unavailable."), "",
      "Grounding: " + markdownText(record.summary ? record.summary.grounding || "Not reported" : "Unavailable"), "",
      "Excerpts show source text; they do not establish that the source is correct or that the question is fully answered.", ""];
    ((record.summary || {}).citations || []).forEach(function (c) {
      lines.push("### [" + c.n + "] " + markdownText(c.title), "", markdownText(c.source), "");
      var url = safeUrl(c.url);
      if (url) lines.push("<" + url.replace(/</g, "%3C").replace(/>/g, "%3E") + ">", "");
      (c.excerpts || []).forEach(function (e) {
        lines.push(e.quote.split(/\r?\n/).map(function (line) { return "> " + markdownText(line); }).join("\n"), "",
          markdownText(e.field) + " offsets " + e.start + "–" + e.end + " (" + markdownText(e.offset_unit || "unicode-code-points") + ")", "");
      });
    });
    lines.push("## Retrieved documents", "");
    record.hits.forEach(function (hit, index) {
      lines.push((index + 1) + ". " + markdownText(hit.document.title) + " — " + markdownText(hit.document.source));
      var url = safeUrl(hit.document.url || hit.document.pdf_url);
      if (url) lines.push("   <" + url.replace(/</g, "%3C").replace(/>/g, "%3E") + ">");
    });
    return lines.join("\n") + "\n";
  }
  return { terms: terms, safeUrl: safeUrl, select: select, summarize: summarize,
    canonical: canonical, createRecord: createRecord, fingerprint: fingerprint, packet: packet, markdown: markdown };
});
