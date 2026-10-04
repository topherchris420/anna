// Anna workbench — backend endpoint configuration (the only place it lives).
//
// The API base is the BACKEND (Flask + Postgres/pgvector) URL — NOT this
// frontend's own URL. `bethesdasearch.vercel.app` / `bethesdasearch.onrender.com`
// are frontends; the backend is a separate service
// (`bethesdasearch-api.onrender.com`, created by render-free.yaml).
//
// Default, from the host the page is served on:
//   - localhost / 127.0.0.1  ->  http://localhost:8000  (docker-compose, ./run,
//                                and deploy/entrypoint-free.sh all serve there)
//   - <name>.onrender.com    ->  https://<name>-api.onrender.com
//   - anything else          ->  PROD_API_BASE (below)
//
// Overrides at runtime (no rebuild needed), highest priority first:
//   1. ?api=https://your-backend.example   (query string)
//   2. Edit ▸ API Endpoint…                (saved in this browser)
//   3. the default above
//
// Scheme + host only — no trailing slash, no /api/v1 suffix (the app appends
// /api/v1 itself). Only http(s) endpoints are accepted: a `javascript:` or
// `data:` value is ignored rather than handed to fetch() or window.open().
(function () {
  "use strict";
  var LOCAL_API_BASE = "http://localhost:8000";
  var PROD_API_BASE = "https://bethesdasearch-api.onrender.com";
  var STORAGE_KEY = "engine_api_base";

  var host = window.location.hostname;
  var isLocal = host === "localhost" || host === "127.0.0.1";

  // "https://api.example/api/v1/" -> "https://api.example"; anything that is
  // not an absolute http(s) URL without credentials -> "".
  function normalizeApiBase(value) {
    var text = String(value == null ? "" : value).trim();
    if (!text) return "";
    var url;
    try {
      url = new URL(text);
    } catch (error) {
      return "";
    }
    if (!/^https?:$/.test(url.protocol) || url.username || url.password) {
      return "";
    }
    var path = url.pathname.replace(/\/+$/, "").replace(/\/api\/v1$/, "");
    return url.origin + path;
  }

  function defaultApiBase() {
    if (isLocal) {
      // The Flask app itself (port 8000) can serve this page too.
      if (window.location.port === "8000") return window.location.origin;
      return LOCAL_API_BASE;
    }
    if (host && host.endsWith(".onrender.com")) {
      if (host.endsWith("-api.onrender.com")) return window.location.origin;
      var appName = host.slice(0, -".onrender.com".length);
      return "https://" + appName + "-api.onrender.com";
    }
    return PROD_API_BASE;
  }

  function readSaved() {
    try {
      return window.localStorage.getItem(STORAGE_KEY) || "";
    } catch (error) {
      return ""; // storage disabled (private mode, policy)
    }
  }

  // { base, source, rejected } — where the endpoint came from is shown in
  // Help ▸ Diagnostics, so "why is it calling that?" has an answer on screen.
  function resolveApiBase() {
    var query = new URLSearchParams(window.location.search).get("api");
    var rejected = [];
    if (query) {
      var fromQuery = normalizeApiBase(query);
      if (fromQuery) return { base: fromQuery, source: "query", rejected: rejected };
      rejected.push({ source: "query", value: query });
    }
    var saved = readSaved();
    if (saved) {
      var fromSaved = normalizeApiBase(saved);
      if (fromSaved) return { base: fromSaved, source: "saved", rejected: rejected };
      rejected.push({ source: "saved", value: saved });
    }
    return { base: defaultApiBase(), source: "default", rejected: rejected };
  }

  function saveApiBase(value) {
    var base = normalizeApiBase(value);
    if (!base) return "";
    try {
      window.localStorage.setItem(STORAGE_KEY, base);
    } catch (error) {
      return "";
    }
    return base;
  }

  function clearSavedApiBase() {
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch (error) {
      /* nothing saved */
    }
  }

  window.AnnaConfig = Object.freeze({
    defaultApiBase: defaultApiBase,
    normalizeApiBase: normalizeApiBase,
    resolveApiBase: resolveApiBase,
    saveApiBase: saveApiBase,
    clearSavedApiBase: clearSavedApiBase,
  });
  // Kept for scripts and docs that read the computed default directly.
  window.ENGINE_API_BASE = defaultApiBase();
})();
