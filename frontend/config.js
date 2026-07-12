// Vers3Dynamics Engineering Intelligence — static frontend configuration.
//
// Environment-aware backend API base: it resolves automatically from the host
// the page is served on, so the same build works locally and in production.
//   - localhost / 127.0.0.1  ->  http://localhost:5000   (local backend)
//   - anything else          ->  the production Render web service
//
// Change PROD_API_BASE if your backend lives elsewhere (custom domain, a
// different Render service name, etc.). Use scheme + host only — NO trailing
// slash and NO /api/v1 suffix (the app appends /api/v1 itself).
//
// This can still be overridden at runtime, in priority order (see app.js):
//   1. ?api=https://your-backend.example  (query string)
//   2. the in-app "API endpoint" setting   (saved to localStorage)
//   3. the value computed below
(function () {
  "use strict";
  var LOCAL_API_BASE = "http://localhost:5000";
  var PROD_API_BASE = "https://bethesdasearch.onrender.com";

  var host = window.location.hostname;
  var isLocal = host === "localhost" || host === "127.0.0.1";

  window.ENGINE_API_BASE = isLocal ? LOCAL_API_BASE : PROD_API_BASE;
})();
