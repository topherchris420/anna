/* Citations for indexed documents: BibTeX entries and plain-text references.
   No services or dependencies; engine/citations.py is the byte-exact Python
   twin (same keys, same escaping, same field order), so a citation copied
   here matches one from GET /api/v1/document/<id>/citation or
   `flask engine cite`. Loaded before evidence.js, which cites the record's
   documents in the Markdown report. */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.EngineCitations = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  /* The sources' display names, as engine/ingest declares them (the registry
     is not available in the browser; test_citations.py keeps these in step). */
  var SOURCE_LABELS = Object.freeze({
    arxiv: "arXiv",
    github: "GitHub",
    nasa: "NASA Technical Reports (NTRS)",
    doe: "DOE OSTI",
    ieee: "IEEE Open Access",
    nist: "NIST Publications",
    linux_kernel: "Linux Kernel Documentation",
    stm32: "STM32 Documentation",
    espressif: "Espressif ESP-IDF Documentation",
    arm: "ARM Developer Documentation",
    riscv: "RISC-V Specifications",
    shadowlibraries: "Shadow Libraries",
  });
  var ENTRY_TYPES = Object.freeze({
    paper: "article",
    report: "techreport",
    standard: "techreport",
    documentation: "manual",
    datasheet: "manual",
  });
  // The same stop words engine/evidence.py and evidence.js use.
  var STOP = new Set(("a an and are as at be been by can could do does for from has have how " +
    "i in is it its of on or should that the their these this to was were " +
    "what when where which who why will with would you your").split(" "));
  var WS = "[ \\t\\n\\r\\f\\v\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff]";
  var WS_RE = new RegExp(WS + "+", "g");
  var YEAR_RE = new RegExp("^" + WS + "*([0-9]{4})");
  var AND_RE = /(?:^| )and(?: |$)/i;
  var HTTP_RE = /^https?:\/\//i;
  var ESCAPES = {
    "\\": "\\textbackslash{}", "{": "\\{", "}": "\\}", "&": "\\&", "%": "\\%",
    "$": "\\$", "#": "\\#", "_": "\\_", "^": "\\^{}", "~": "\\~{}",
  };

  function get(doc, name, fallback) {
    var value = doc && typeof doc === "object" ? doc[name] : undefined;
    return value == null ? fallback : value;
  }
  function clean(text) {
    if (typeof text !== "string") return "";
    return text.replace(WS_RE, " ").replace(/^ +| +$/g, "");
  }
  function field(doc, name) { return clean(get(doc, name, "")); }
  function authorsOf(doc) {
    var value = get(doc, "authors", []);
    if (!Array.isArray(value)) return [];
    return value.filter(function (a) { return typeof a === "string" && clean(a); }).map(clean);
  }
  function identifiersOf(doc) {
    var value = get(doc, "identifiers", {});
    var out = {};
    if (!value || typeof value !== "object" || Array.isArray(value)) return out;
    Object.keys(value).forEach(function (key) {
      if (typeof value[key] === "string" && clean(value[key])) out[String(key)] = clean(value[key]);
    });
    return out;
  }

  /* ---------------------------------------------------------------- keys */
  function slug(text) {
    return String(text || "").normalize("NFKD").toLowerCase().replace(/[^a-z0-9]/g, "");
  }
  function slugTokens(text) {
    return String(text || "").normalize("NFKD").toLowerCase().match(/[a-z0-9]+/g) || [];
  }
  function surname(author) {
    author = clean(author);
    var head;
    if (author.indexOf(",") >= 0) head = author.split(",")[0];
    else { var parts = author.split(" "); head = parts.length ? parts[parts.length - 1] : ""; }
    return slug(head);
  }
  function yearOf(published) {
    var match = YEAR_RE.exec(typeof published === "string" ? published : "");
    return match ? match[1] : "";
  }
  function titleWord(title) {
    var tokens = slugTokens(title);
    for (var i = 0; i < tokens.length; i += 1) {
      if (tokens[i].length >= 3 && !STOP.has(tokens[i])) return tokens[i];
    }
    return tokens.length ? tokens[0] : "untitled";
  }
  function bibtexKey(doc) {
    var authors = authorsOf(doc);
    var who = authors.length ? surname(authors[0]) : "";
    if (!who) who = slug(field(doc, "source"));
    if (!who) who = "anna";
    var year = yearOf(get(doc, "published", "")) || "nd";
    return who.slice(0, 32) + year + titleWord(field(doc, "title")).slice(0, 32);
  }

  /* -------------------------------------------------------------- BibTeX */
  function escape(text) {
    return clean(text).split("").map(function (ch) {
      return Object.prototype.hasOwnProperty.call(ESCAPES, ch) ? ESCAPES[ch] : ch;
    }).join("");
  }
  function sourceLabel(source) {
    source = clean(source);
    return Object.prototype.hasOwnProperty.call(SOURCE_LABELS, source) ? SOURCE_LABELS[source] : source;
  }
  function authorField(author) {
    var value = escape(author);
    return AND_RE.test(author) ? "{" + value + "}" : value;
  }
  function link(doc) {
    var names = ["url", "pdf_url"];
    for (var i = 0; i < names.length; i += 1) {
      var value = get(doc, names[i], "");
      if (typeof value !== "string") continue;
      value = value.replace(WS_RE, "");
      if (HTTP_RE.test(value)) {
        return value.replace(/\\/g, "%5C").replace(/\{/g, "%7B").replace(/\}/g, "%7D");
      }
    }
    return "";
  }
  function bibtex(doc, key) {
    var kind = field(doc, "kind");
    var entryType = Object.prototype.hasOwnProperty.call(ENTRY_TYPES, kind) ? ENTRY_TYPES[kind] : "misc";
    var source = field(doc, "source");
    var label = sourceLabel(source);
    var identifiers = identifiersOf(doc);
    var authors = authorsOf(doc);
    var fields = [];
    if (authors.length) fields.push(["author", authors.map(authorField).join(" and ")]);
    fields.push(["title", "{" + escape(field(doc, "title") || "Untitled") + "}"]);
    var eprint = identifiers.arxiv_id || "";
    if (entryType === "article" && source === "arxiv") {
      fields.push(["journal", escape("arXiv preprint" + (eprint ? " arXiv:" + eprint : ""))]);
    } else if (entryType === "techreport" && label) {
      fields.push(["institution", escape(label)]);
    } else if (entryType === "manual" && label) {
      fields.push(["organization", escape(label)]);
    } else if (entryType === "misc" && label) {
      fields.push(["howpublished", escape(label)]);
    }
    var year = yearOf(get(doc, "published", ""));
    if (year) fields.push(["year", year]);
    var version = field(doc, "version");
    if (version) fields.push(["version", escape(version)]);
    if (identifiers.doi) fields.push(["doi", escape(identifiers.doi)]);
    if (eprint) { fields.push(["eprint", escape(eprint)]); fields.push(["archivePrefix", "arXiv"]); }
    var url = link(doc);
    if (url) fields.push(["url", url]);
    var id = field(doc, "id");
    if (id) fields.push(["annaid", escape(id)]);
    var lines = ["@" + entryType + "{" + (key || bibtexKey(doc)) + ","];
    fields.forEach(function (pair, index) {
      lines.push("  " + pair[0] + " = {" + pair[1] + "}" + (index < fields.length - 1 ? "," : ""));
    });
    lines.push("}");
    return lines.join("\n");
  }

  /* ---------------------------------------------------------- plain text */
  function joinNames(names) {
    if (names.length === 1) return names[0];
    if (names.length === 2) return names[0] + " and " + names[1];
    return names.slice(0, -1).join(", ") + ", and " + names[names.length - 1];
  }
  function plain(doc) {
    var authors = authorsOf(doc);
    var title = field(doc, "title") || "Untitled";
    var year = yearOf(get(doc, "published", "")) || "n.d.";
    var head = authors.length ? joinNames(authors) + " (" + year + "). " + title : title + " (" + year + ")";
    if (".?!".indexOf(head[head.length - 1]) < 0) head += ".";
    var parts = [head];
    var venue = sourceLabel(field(doc, "source"));
    var version = field(doc, "version");
    if (version) venue += (venue ? ", " : "") + "version " + version;
    if (venue) parts.push(".?!".indexOf(venue[venue.length - 1]) >= 0 ? venue : venue + ".");
    var url = link(doc);
    if (url) parts.push(url);
    return parts.join(" ");
  }

  /* ---------------------------------------------- several documents at once */
  function uniqueKey(base, occurrence) {
    if (occurrence === 1) return base;
    if (occurrence <= 26) return base + String.fromCharCode(97 + occurrence - 1);
    return base + "-" + occurrence;
  }
  function citations(docs) {
    var seen = Object.create(null);
    return Array.from(docs || []).map(function (doc) {
      var base = bibtexKey(doc);
      var occurrence = (seen[base] || 0) + 1;
      seen[base] = occurrence;
      var key = uniqueKey(base, occurrence);
      return { id: field(doc, "id"), key: key, bibtex: bibtex(doc, key), text: plain(doc) };
    });
  }
  function citation(doc) { return citations([doc])[0]; }
  function bibliography(docs) {
    var entries = citations(docs).map(function (c) { return c.bibtex; });
    return entries.length ? entries.join("\n\n") + "\n" : "";
  }

  return { SOURCE_LABELS: SOURCE_LABELS, ENTRY_TYPES: ENTRY_TYPES, clean: clean, slug: slug,
    surname: surname, yearOf: yearOf, titleWord: titleWord, bibtexKey: bibtexKey, escape: escape,
    sourceLabel: sourceLabel, link: link, bibtex: bibtex, plain: plain, citations: citations,
    citation: citation, bibliography: bibliography };
});
