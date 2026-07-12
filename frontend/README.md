# Engineering Intelligence — static frontend

A framework-free, single-page static frontend for the Vers3Dynamics Engineering
Intelligence REST API. It runs on any static host (Vercel, Dappling Network,
Netlify, GitHub Pages, S3/CloudFront) and talks to a **separately deployed
backend** over `/api/v1`.

> The backend (Flask + Elasticsearch + PostgreSQL + Redis) must be deployed
> first — see [`../docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md). This folder is
> only the browser UI.

## Files

| File | Purpose |
|---|---|
| `index.html` | Markup |
| `styles.css` | Styles (same design system as the server UI) |
| `app.js` | Search, facets, mode toggle, and AI-answer logic |
| `config.js` | **Default backend API URL** (edit this) |
| `vercel.json` | Vercel static config + security headers |

## Point it at your backend

Three ways, in priority order:

1. **Query string** — `https://your-frontend/?api=https://your-backend.onrender.com`
2. **In-app setting** — click the ⚙︎ button (top-right), paste the URL, Save
   (stored in `localStorage`).
3. **`config.js`** — set the default that ships with the deploy:
   ```js
   window.ENGINE_API_BASE = "https://your-backend.onrender.com";
   ```
   Use the scheme + host only — no trailing slash, no `/api/v1` suffix.

## Deploy to Vercel

**Recommended — Git integration (no per-project setup).** The repo root ships a
[`vercel.json`](../vercel.json) with `outputDirectory: "frontend"`, so a Vercel
project connected to this repo via **dashboard Git integration** automatically
serves this static frontend (instead of trying to build the Python backend at
the root). If you already connected the repo in Vercel, it just works on the
next push — nothing to configure. Then set the backend URL (`config.js`, `?api=`,
or the in-app ⚙︎) and add the frontend origin to the backend's CORS allow-list.

**Alternative — deploy this folder as its own project.** If you prefer, import
the repo and set **Root Directory = `frontend`** (framework *Other*, no build
command, output `.`); it then uses this folder's `vercel.json`.

## Manual deploy via GitHub Actions (fallback)

[`.github/workflows/deploy-frontend.yml`](../.github/workflows/deploy-frontend.yml)
is a **manual** fallback (run it from the **Actions** tab) for deploying via the
Vercel CLI — useful if you disconnect the dashboard Git integration. It does
**not** run on push, so it never double-deploys alongside the Git integration.

One-time setup (only needed if you use this workflow):

1. Create the Vercel project locally (from this folder), which writes the IDs:
   ```bash
   cd frontend
   npx vercel link        # choose/create the project; framework: Other
   cat .vercel/project.json   # note "orgId" and "projectId"
   ```
2. Create a Vercel token: Vercel → **Account Settings → Tokens**.
3. In GitHub → **Settings → Secrets and variables → Actions**, add:
   | Secret | Value |
   |---|---|
   | `VERCEL_TOKEN` | the token from step 2 |
   | `VERCEL_ORG_ID` | `orgId` from `project.json` |
   | `VERCEL_PROJECT_ID` | `projectId` from `project.json` |
4. Run it: **Actions → Deploy frontend to Vercel (manual) → Run workflow**
   (choose production or preview). The deploy URL is printed in the job summary.

> The workflow **skips gracefully** (green, with a warning) if the secrets are
> absent, and is manual-only, so it never double-deploys with the dashboard Git
> integration. Use whichever single path you prefer.

## Deploy to Render (Static Site)

Render Static Sites publish a *subdirectory*, so `npm run build` here assembles
the static assets into `build/` for exactly that.

- **Root Directory:** `frontend`
- **Build Command:** `npm run build` (or `yarn build`)
- **Publish Directory:** `build`

That's it — no framework, no server. (Alternatively, skip the build entirely by
setting **Publish Directory = `.`** with an empty build command.) Remember this
serves only the UI; point it at a running backend via `config.js` / `?api=` and
add its origin to the backend's CORS allow-list.

## Deploy to Dappling Network

Dappling deploys static frontends to decentralized (IPFS) infrastructure.

1. Connect this repo in Dappling.
2. **Base/Root directory:** `frontend`
3. **Framework:** *Static* (or "Other") · **Build command:** *(none / `npm run
   build`)* · **Publish directory:** `frontend` (or `.` if the base is already
   `frontend`).
4. Deploy, then set the backend URL as above and allow the origin in CORS.

## Enable CORS on the backend

The API emits CORS headers. By default it allows any origin (`*`), which is fine
to get started. To restrict it to your deployed frontends, set on the backend:

```
ENGINE_CORS_ORIGINS=https://your-app.vercel.app,https://your-app.on-dappling.network
```

(For Render, add this as an environment variable on the `engineering-intelligence`
service.) After the backend is reachable, the ⚙︎ panel shows a green
"✓ connected · N docs" when the endpoint is correct.

## Local preview

```bash
cd frontend
python3 -m http.server 5173
# open http://localhost:5173/?api=http://localhost:8000
```
