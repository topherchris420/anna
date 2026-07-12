// Vers3Dynamics Engineering Intelligence — static frontend configuration.
//
// Point this at your deployed backend API (the Render/Fly/VPS URL from
// docs/DEPLOYMENT.md). Include the scheme and host, WITHOUT a trailing slash
// and WITHOUT the /api/v1 suffix — the app appends /api/v1 itself.
//
// This default can be overridden at runtime, in priority order:
//   1. ?api=https://your-backend.example  (query string)
//   2. the in-app "API endpoint" setting   (saved to localStorage)
//   3. this value
window.ENGINE_API_BASE = "http://localhost:8000";
