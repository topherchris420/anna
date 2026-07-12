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

Deploy this folder as its **own** Vercel project (do **not** point Vercel at the
repo root — that's the Python backend and will fail to build).

1. Vercel → **Add New → Project** → import this repo.
2. **Root Directory:** `frontend`
3. **Framework Preset:** *Other*  · **Build Command:** *(empty)*  ·
   **Output Directory:** `.`
4. Deploy. Then set `config.js`'s `ENGINE_API_BASE` to your backend URL (or use
   `?api=`), and add the frontend's origin to the backend's CORS allow-list
   (below).

## Auto-deploy via GitHub Actions

[`.github/workflows/deploy-frontend.yml`](../.github/workflows/deploy-frontend.yml)
deploys this folder to Vercel automatically — **production on push to `main`**,
a **preview** on pull requests — but only when files under `frontend/` change.

One-time setup:

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
4. Push a change under `frontend/` (or run the workflow manually via
   **Actions → Deploy frontend to Vercel → Run workflow**). The deploy URL is
   printed in the job summary.

> The workflow **skips gracefully** (green, with a warning) until those secrets
> exist, so it never breaks CI.
>
> To avoid double deploys, either use this Action **or** Vercel's dashboard Git
> integration — not both. If you linked the repo in the dashboard, set that
> project's **Root Directory** to `frontend` and disable its auto-deploys, or
> just rely on this workflow.

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
