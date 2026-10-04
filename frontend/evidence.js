/* Source excerpts, portable research records, and record verification.
   No services or dependencies; engine/records.py is the byte-exact Python twin. */
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
        var entry = { score: hit.score, explanation: hit.explanation || null,
          document: { id: doc.id, title: doc.title, source: doc.source,
            url: safeUrl(doc.url), pdf_url: safeUrl(doc.pdf_url), authors: doc.authors || [],
            published: doc.published || "", version: doc.version || "", abstract: doc.abstract || "" },
          highlights: hit.highlights || [] };
        // Only backends that know their score ceiling report relevance; never invent it.
        if (hit.relevance != null) entry.relevance = hit.relevance;
        return entry;
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
  /* ---------------------------------------------------------------- verification
     Mirrors engine/records.py status for status, so a record checked in the
     browser, by the CLI, or by the API reads the same way. */
  var SCHEMA = "anna-research-record/v1";
  var OFFSET_UNITS = ["unicode-code-points", "utf-16"];
  var EXCERPT_FIELDS = ["abstract", "body", "title"];
  var STATUSES = ["verified", "relocated", "drifted", "missing-document", "missing-field", "invalid-excerpt"];
  var SHA256 = /^[0-9a-f]{64}$/i;
  function isInt(value) { return typeof value === "number" && Number.isInteger(value); }
  function unwrapPacket(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("a JSON object is required: a research record or its exported packet");
    }
    if (payload.record && typeof payload.record === "object" && !Array.isArray(payload.record)) {
      var declared = payload.content_sha256;
      if (declared != null) {
        if (typeof declared !== "string" || !SHA256.test(declared)) {
          throw new Error("content_sha256 must be a 64-character hexadecimal SHA-256 digest");
        }
        declared = declared.toLowerCase();
      }
      return { record: payload.record, declared: declared == null ? null : declared };
    }
    if ("schema" in payload || "summary" in payload || "hits" in payload) return { record: payload, declared: null };
    throw new Error("not a research record: expected a packet with a 'record' object or a record with 'schema', 'summary' and 'hits'");
  }
  function iterExcerpts(record) {
    var pairs = [];
    var summary = record && record.summary;
    var citations = summary && Array.isArray(summary.citations) ? summary.citations : [];
    citations.forEach(function (citation) {
      if (!citation || typeof citation !== "object") return;
      (Array.isArray(citation.excerpts) ? citation.excerpts : []).forEach(function (excerpt) {
        pairs.push({ citation: citation.n, excerpt: excerpt });
      });
    });
    return pairs;
  }
  /* Slice in the record's own offset convention. JavaScript strings are UTF-16,
     so code-point offsets (engine records) go through Array.from. */
  function sliceField(text, start, end, unit) {
    if (unit === "utf-16") return text.slice(start, end);
    return Array.from(text).slice(start, end).join("");
  }
  function locate(text, quote, unit) {
    var index = text.indexOf(quote);
    if (index < 0) return null;
    return unit === "utf-16" ? index : Array.from(text.slice(0, index)).length;
  }
  function excerptProblem(excerpt) {
    if (!excerpt || typeof excerpt !== "object") return "excerpt must be an object";
    if (typeof excerpt.document_id !== "string" || !excerpt.document_id.trim()) return "document_id must be a nonempty string";
    if (EXCERPT_FIELDS.indexOf(excerpt.field) < 0) return "field must be one of " + EXCERPT_FIELDS.join(", ");
    if (typeof excerpt.quote !== "string" || !excerpt.quote) return "quote must be a nonempty string";
    var unit = excerpt.offset_unit == null ? "unicode-code-points" : excerpt.offset_unit;
    if (OFFSET_UNITS.indexOf(unit) < 0) return "offset_unit must be one of " + OFFSET_UNITS.join(", ");
    if (!isInt(excerpt.start)) return "start must be an integer";
    if (!isInt(excerpt.end)) return "end must be an integer";
    if (excerpt.start < 0 || excerpt.end <= excerpt.start) return "offsets must satisfy 0 <= start < end";
    return null;
  }
  function verifyExcerpt(excerpt, doc, citation) {
    var report = { citation: citation == null ? null : citation };
    if (excerpt && typeof excerpt === "object") {
      ["document_id", "field", "start", "end"].forEach(function (key) {
        report[key] = key in excerpt ? excerpt[key] : null;
      });
      report.offset_unit = excerpt.offset_unit == null ? "unicode-code-points" : excerpt.offset_unit;
    }
    var problem = excerptProblem(excerpt);
    if (problem) { report.status = "invalid-excerpt"; report.detail = problem; return report; }
    if (doc == null) { report.status = "missing-document"; return report; }
    var text = doc[excerpt.field];
    if (typeof text !== "string") { report.status = "missing-field"; return report; }
    if (sliceField(text, excerpt.start, excerpt.end, report.offset_unit) === excerpt.quote) {
      report.status = "verified"; return report;
    }
    var found = locate(text, excerpt.quote, report.offset_unit);
    if (found == null) report.status = "drifted";
    else { report.status = "relocated"; report.found_at = found; }
    return report;
  }
  /* getDocument(id) returns the current document (plain object) or null. */
  function verifyExcerpts(record, getDocument) {
    var cache = new Map();
    return iterExcerpts(record).map(function (pair) {
      var doc = null;
      if (!excerptProblem(pair.excerpt)) {
        var id = pair.excerpt.document_id;
        if (!cache.has(id)) cache.set(id, getDocument(id));
        doc = cache.get(id);
      }
      return verifyExcerpt(pair.excerpt, doc, pair.citation);
    });
  }
  function countStatuses(reports) {
    var counts = {};
    STATUSES.forEach(function (status) { counts[status] = 0; });
    reports.forEach(function (report) { counts[report.status] = (counts[report.status] || 0) + 1; });
    return counts;
  }
  /* Full report, same shape as POST /api/v1/evidence/verify. Without
     getDocument only the fingerprint is checked; without crypto.subtle (plain
     HTTP away from localhost) the fingerprint is reported as not computed. */
  async function verifyRecord(payload, getDocument, options) {
    options = options || {};
    var unwrapped = unwrapPacket(payload);
    var record = unwrapped.record;
    var computed = null;
    try { computed = await fingerprint(record, options.cryptoApi); } catch (_) { computed = null; }
    var matches = unwrapped.declared == null || computed == null ? null : unwrapped.declared === computed;
    var reports = typeof getDocument === "function" ? verifyExcerpts(record, getDocument) : [];
    return {
      schema: typeof record.schema === "string" ? record.schema : null,
      schema_known: record.schema === SCHEMA,
      fingerprint: { declared: unwrapped.declared, computed: computed, matches: matches },
      checked_against: options.checkedAgainst || null,
      excerpts: reports,
      counts: countStatuses(reports),
      ok: matches !== false && reports.every(function (report) { return report.status === "verified"; }),
    };
  }
  function markdownText(value) {
    return String(value == null ? "" : value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/([\\`*_{}\[\]()#+.!|~-])/g, "\\$1");
  }
  function markdown(record, options) {
    options = options || {};
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
      lines.push((index + 1) + ". " + markdownText(hit.document.title) + " — " + markdownText(hit.document.source) +
        (hit.relevance != null ? " · relevance " + Number(hit.relevance).toFixed(2) : ""));
      var url = safeUrl(hit.document.url || hit.document.pdf_url);
      if (url) lines.push("   <" + url.replace(/</g, "%3C").replace(/>/g, "%3E") + ">");
    });
    if (options.fingerprint) {
      lines.push("", "## Verification", "",
        "Content fingerprint (SHA-256 of the canonical JSON record): `" + String(options.fingerprint).replace(/[^0-9a-f]/gi, "") + "`", "",
        "Re-check the matching `.json` export with File ▸ Verify research record…, " +
        "`flask engine verify-record`, or `POST /api/v1/evidence/verify`.");
    }
    return lines.join("\n") + "\n";
  }
  return { terms: terms, safeUrl: safeUrl, select: select, summarize: summarize,
    canonical: canonical, createRecord: createRecord, fingerprint: fingerprint, packet: packet, markdown: markdown,
    SCHEMA: SCHEMA, STATUSES: STATUSES, unwrapPacket: unwrapPacket, sliceField: sliceField, locate: locate,
    verifyExcerpt: verifyExcerpt, verifyExcerpts: verifyExcerpts, countStatuses: countStatuses, verifyRecord: verifyRecord };
});
