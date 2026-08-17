// Vers3Dynamics Engineering Intelligence — static frontend configuration.
//
// The API base is the BACKEND (Flask + Postgres/pgvector) URL — NOT this
// frontend's own URL. `bethesdasearch.onrender.com` is the frontend (a Render
// Static Site / Vercel / Dappling); the backend is a separate service
// (`bethesdasearch-api.onrender.com`, created by render-free.yaml).
//
// Environment-aware, resolved from the host the page is served on:
//   - localhost / 127.0.0.1  ->  http://localhost:5000            (local backend)
//   - anything else          ->  the production backend (below)
//
// Change PROD_API_BASE if your backend lives elsewhere (custom domain, a
// different Render service name, etc.). Scheme + host only — NO trailing slash,
// NO /api/v1 suffix (the app appends /api/v1 itself).
//
// Runtime overrides (no rebuild needed), in priority order (see app.js):
//   1. ?api=https://your-backend.example  (query string)
//   2. the in-app "API endpoint" setting   (saved to localStorage)
//   3. the value computed below
(function () {
  "use strict";
  var LOCAL_API_BASE = "http://localhost:5000";
  var PROD_API_BASE = "https://bethesdasearch-api.onrender.com";

  var host = window.location.hostname;
  var isLocal = host === "localhost" || host === "127.0.0.1";

  function resolveDefaultApiBase() {
    if (isLocal) {
      if (window.location.port === "5000") return window.location.origin;
      return LOCAL_API_BASE;
    }
    if (host && host.endsWith(".onrender.com")) {
      if (host.endsWith("-api.onrender.com")) return window.location.origin;
      var appName = host.slice(0, -".onrender.com".length);
      return "https://" + appName + "-api.onrender.com";
    }
    return PROD_API_BASE;
  }

  window.ENGINE_API_BASE = resolveDefaultApiBase();
})();
