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

## Deploy to Netlify

**Git integration (zero-config).** The repo root ships a
[`netlify.toml`](../netlify.toml) that publishes this folder directly with no
build step, so a Netlify site connected to this repo (**Add new site → Import
an existing project**) just works — accept the detected settings and deploy.
Then set the backend URL (`config.js`, `?api=`, or the in-app ⚙︎) and add the
site's origin (`https://your-site.netlify.app`) to the backend's CORS
allow-list.

**CLI alternative** (one-off deploys, no Git connection):

```bash
npx netlify-cli deploy --dir frontend --prod
```

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

## Deploy to Dappling Network (IPFS)

[dAppling](https://dappling.network) hosts static frontends on decentralized
(IPFS) infrastructure. There is **no repo config file** — it's configured in the
dashboard. This frontend is IPFS-ready: every asset path is relative and the SPA
keeps its state in the query string (no client-side path routing), so it works
under an IPFS gateway/CID path or a custom/ENS domain.

1. dAppling dashboard → **New Project** → connect this repo.
2. Set (**Framework Preset: No Framework**), using **any** config below — each
   one puts an `index.html` in the served Output Directory:

   | | Root Directory | Build Command | Output Directory |
   |---|---|---|---|
   | **A — zero-config root** (default) | *(leave empty)* | *(leave empty)* | `.` |
   | **B — built into `dist`** | *(leave empty)* | `npm run build` | `dist` |
   | **C — from the subfolder** | `frontend` | *(leave empty)* | `.` |

   Config **A** works with dAppling's default (serve the repo root) because the
   repo root now ships an [`index.html`](../index.html) that hands off to
   `frontend/` — no build step required. Config **B** uses the repo-root
   [`package.json`](../package.json) build, which assembles the site into
   `./dist`. Config **C** serves `frontend/` directly.
3. **Deploy.** dAppling builds (if a Build Command is set), pins the output to
   IPFS, and gives you a URL/domain.
4. The backend URL is already baked into `config.js`
   (`https://bethesdasearch-api.onrender.com`), so search works immediately. To use a
   different backend without rebuilding, append `?api=https://…` or use the ⚙︎
   setting. Then add your dAppling domain to the backend's CORS allow-list (below).

> **Getting `index.html not found in ., … exiting`?** Your build was using an
> **Output Directory** of `.` with nothing built there. The repo root now ships
> a redirect `index.html` (config **A** above), so a fresh deploy with an empty
> Build Command and Output Directory `.` works out of the box — just redeploy.
> For a self-contained output that doesn't serve the whole repo, use config
> **B** (`npm run build` → `dist`) or **C** (Root Directory `frontend`).

> dAppling env vars apply at **build time** only. This frontend resolves its API
> URL at **runtime**, so you don't need any — just edit `config.js` (baked in) or
> use `?api=` / ⚙︎. Prefer the domain dAppling assigns over a raw
> `/ipfs/<CID>/…` gateway path so relative asset links always resolve.

## Enable CORS on the backend

The API emits CORS headers. By default it allows any origin (`*`), which is fine
to get started. To restrict it to your deployed frontends, set on the backend:

```
ENGINE_CORS_ORIGINS=https://your-app.vercel.app,https://your-app.dappling.network
```

(For Render, add this as an environment variable on the `bethesdasearch-api`
backend web service — or leave the default `*`.) After the backend is reachable, the ⚙︎ panel
shows a green "✓ connected · N docs" when the endpoint is correct.

## Local preview

```bash
cd frontend
python3 -m http.server 5173
# open http://localhost:5173/?api=http://localhost:8000
```
