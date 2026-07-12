# Deployment

The Vers3Dynamics Engineering Intelligence platform is a **full-stack, stateful
application**: Flask/gunicorn + a background worker, backed by **Elasticsearch**
(search), **PostgreSQL** (collections), and **Redis** (task queue). It must run
on a host that supports long-running services and Docker.

## Why not Vercel or Dappling Network?

Those platforms host **frontends**, not this backend:

- **Vercel** runs serverless functions and static sites. It cannot run gunicorn
  + Celery as long-lived processes, and it cannot host Elasticsearch, Postgres,
  or Redis. Connecting this repo to Vercel produces a failed build / error page
  because there is no Vercel-compatible project here — search fundamentally
  needs a running Elasticsearch cluster.
- **Dappling Network** is decentralized static/frontend hosting (IPFS-style). It
  serves static assets only; there is no backend to run the search API.

If you specifically want a frontend on Vercel/Dappling, that's fully supported:
deploy the backend from this guide, then deploy the included **static frontend**
in [`../frontend/`](../frontend/) to Vercel/Dappling and point it at the
backend's `/api/v1` API. See [Option C](#option-c--static-frontend-on-verceldappling)
below.

---

## Option A — Render (one Blueprint, recommended)

[`render.yaml`](../render.yaml) declares the whole backend. Render provisions it
from one file — **use Blueprint, not a manually-created Web Service or Static
Site.** (A Static Site can only serve the frontend; it fails on this backend.)

**Step by step:**

1. Push this repo to GitHub (done for your fork).
2. [Render dashboard](https://dashboard.render.com) → **New ▾ → Blueprint**.
3. **Connect** the `topherchris420/anna` repo. Render reads `render.yaml`.
4. Pick the **`main`** branch → **Apply**. Render creates and links:
   | Service | Type | Purpose |
   |---|---|---|
   | `engineering-intelligence` | Web | UI at `/`, REST API at `/api/v1` |
   | `engine-elasticsearch` | Private | Elasticsearch 8.5 (BM25 + kNN) + disk |
   | `engine-postgres` | Postgres | collections/bookmarks |
   | `engine-worker` | Worker | background ingestion *(optional)* |
   | `engine-redis` | Redis | Celery broker *(optional)* |
5. Wait for **all** services to go green. The web image builds `deploy/Dockerfile`
   (a few minutes; the torch download is the slow part). Elasticsearch takes
   ~30–60s on first boot — if the web service flaps because ES wasn't ready yet,
   just **Manual Deploy → Deploy latest commit** on the web service once ES is up.
6. **Load data.** Open the **web** service → **Shell**:
   ```bash
   flask engine index-init          # create the hybrid ES index
   flask engine collections-init    # create the collections tables
   flask engine demo                # index a few offline sample docs
   # then real data:
   flask engine ingest arxiv  -q "cat:eess.SY" -n 500
   flask engine ingest github -q "topic:rtos stars:>1000" -n 200
   ```
7. Your backend is `https://engineering-intelligence.onrender.com`. Confirm it:
   `GET /api/v1/health` should report `"elasticsearch": "ok"` and a document count.

**Point the frontend at it.** `frontend/config.js` already defaults `PROD_API_BASE`
to `https://engineering-intelligence.onrender.com`. If Render appended a suffix
because that name was taken (check the web service's URL), update that one line —
or just use the in-app ⚙︎ setting / `?api=` without redeploying.

### Cost reality & genuinely zero-cost alternatives

This blueprint is **not free**: Elasticsearch needs ~2 GB RAM, so it runs on
paid instances (≈ $25/mo for web + ≈ $25/mo for ES; Postgres/Redis add a little).
There is **no free Elasticsearch tier** on Render. Options:

- **Trim it:** delete the `engine-worker` + `engine-redis` services (and the
  `REDIS_URL` env on web) — search and synchronous `flask engine ingest` still
  work. Use `plan: free` Postgres. That leaves just web + ES.
- **Truly $0, self-hosted:** run the repo's `docker-compose.yml` on a free VM
  (e.g. **Oracle Cloud Always Free** gives a 4-core / 24 GB ARM box for $0), and
  expose it with a free **Cloudflare Tunnel**. This runs the *entire* stack,
  Elasticsearch included, for nothing — matching a "zero-cost self-hosting" goal.
- **Free PaaS, no Elasticsearch:** swap Elasticsearch for **PostgreSQL full-text
  search + `pgvector`**, which fits Render's free web + free Postgres. This is a
  drop-in retriever change (the engine already abstracts the backend). Ask and
  it can be added as `engine/search_pg.py`.

### Plans & memory

- **Elasticsearch** and the **web** service default to Render's **Standard**
  plan (~2 GB RAM). Do **not** put Elasticsearch on a free/starter plan — it
  will OOM. The web service loads a local embedding model (~0.5–1 GB); if you
  want to run cheaper, set `ENGINE_EMBEDDING_FALLBACK=true` (keyword + hashing
  vectors only, no ML model, much lower memory).
- Adjust `ES_JAVA_OPTS` heap and the `es-data` disk size in `render.yaml` to
  your corpus size.

### Environment variables (wired automatically by the Blueprint)

| Variable | Source | Purpose |
|---|---|---|
| `SECRET_KEY` | generated | Flask secret |
| `ELASTICSEARCH_HOST` | `http://engine-elasticsearch:9200` | ES internal URL |
| `DATABASE_URL` | `engine-postgres` | Collections DB (scheme auto-normalized) |
| `REDIS_URL` | `engine-redis` | Celery broker/result backend |
| `ENGINE_INDEX` | `engineering_docs` | ES index name |
| `ENGINE_EMBEDDING_FALLBACK` | `false` | Set `true` for low-memory mode |
| `GITHUB_TOKEN` / `IEEE_API_KEY` | (add yourself) | Optional source credentials |

> If Elasticsearch is unreachable after deploy, confirm the internal hostname
> and port Render assigned to `engine-elasticsearch` and update
> `ELASTICSEARCH_HOST` accordingly.

---

## Option A-free — $0 on Render (Postgres FTS + pgvector, no Elasticsearch)

The [`render-free.yaml`](../render-free.yaml) blueprint runs the platform for
**free**: one free web service + one free PostgreSQL database with the
`pgvector` extension. Search uses `ENGINE_BACKEND=postgres` — Postgres full-text
search fused with pgvector kNN via the same RRF and the same REST API/frontend.
No Elasticsearch, no paid instances.

1. Render dashboard → **New ▾ → Blueprint** → pick the repo.
2. Set the **blueprint file** to **`render-free.yaml`** → **Apply**. It creates
   the **backend** (keep it distinct from the frontend, which is your
   `bethesdasearch` Static Site at `bethesdasearch.onrender.com`):
   - `bethesdasearch-api` — free web service → `https://bethesdasearch-api.onrender.com`
   - `bethesda-postgres` — free PostgreSQL (pgvector)
3. When green, open the web service **Shell** and load data:
   ```bash
   flask engine index-init          # creates the table, pgvector, FTS index
   flask engine collections-init
   flask engine demo                # offline sample docs
   flask engine ingest arxiv -q "cat:eess.SY" -n 500
   ```
4. `frontend/config.js` already points `PROD_API_BASE` at
   `https://bethesdasearch-api.onrender.com`, so the deployed frontend just works.
   Verify with `GET /api/v1/health` → `"backend": "postgres"` and a doc count.

**Tradeoffs of the free tier:**

- Embeddings run in the **deterministic fallback** (`ENGINE_EMBEDDING_FALLBACK=true`)
  because a real transformer won't fit the 512 MB free instance. So the
  **lexical (FTS) half carries relevance**, and vectors add a weak, consistent
  signal. For true semantic vectors, switch the web service to
  `deploy/Dockerfile` (with torch) on a ≥1 GB instance and set the flag to
  `false` (both ingest and query must use the same embedder).
- Free web services **sleep after ~15 min idle** and cold-start (~30–60 s).
- Free Postgres is **removed after ~30 days** — move `plan: free` → `basic-256mb`
  to keep it.

> This is the same code as the Elasticsearch path — only `ENGINE_BACKEND`
> differs. You can start free on Postgres and later flip to Elasticsearch
> (Option A) without touching the frontend or API.

## Option B — Fly.io / Railway / VPS

The same `deploy/Dockerfile` runs anywhere Docker does.

- **Fly.io**: `fly launch` with `deploy/Dockerfile`; add Elasticsearch and
  Postgres as separate Fly apps (or use Fly Postgres + a managed ES like
  Elastic Cloud / Bonsai) and set `ELASTICSEARCH_HOST`, `DATABASE_URL`,
  `REDIS_URL`.
- **Railway**: deploy the Dockerfile as a service; add PostgreSQL and Redis
  plugins; run Elasticsearch as a service from the official image.
- **VPS (DigitalOcean/Hetzner/EC2)**: the repo's root `docker-compose.yml`
  already defines every service. On the box:
  ```bash
  cp .env.dev .env    # then edit secrets/ports for production
  docker compose --profile web --profile worker --profile elasticsearch \
                 --profile postgres --profile redis up -d --build
  docker compose exec web flask engine index-init
  ```
  Put nginx/Caddy in front for TLS.

### Using a managed Elasticsearch

If you use Elastic Cloud, Bonsai, or another managed ES with authentication,
set `ELASTICSEARCH_HOST` to the endpoint. Managed clusters usually require an
API key or basic auth — extend `engine/index.py::get_client` to pass
`api_key=`/`basic_auth=` from environment variables.

---

## Option C — Static frontend on Vercel/Dappling

You *can* use Vercel or Dappling Network — for the **frontend only**, talking to
a backend deployed via Option A/B. The repo ships a framework-free static SPA in
[`../frontend/`](../frontend/) that calls the `/api/v1` REST API.

1. Deploy the backend (Option A/A-free/B) and note its URL, e.g.
   `https://bethesdasearch-api.onrender.com`.
2. **Allow the frontend origin in CORS.** On the backend, set:
   ```
   ENGINE_CORS_ORIGINS=https://your-app.vercel.app,https://your-app.dappling.network
   ```
   (Default is `*`, which works immediately but allows any origin.) On Render,
   add this as an env var on the `bethesdasearch-api` web service.
3. **Deploy the frontend** (pick one host):
   - **Vercel** — dashboard Git integration: the repo-root
     [`../vercel.json`](../vercel.json) has `outputDirectory: "frontend"`, so a
     connected project serves the frontend on every push, no settings changes.
   - **Dappling (IPFS)** — dashboard only (no config file), **Framework Preset**
     *No Framework*. Either: **Root Directory** empty · **Build** `npm run build`
     · **Output** `dist` (uses the repo-root `package.json`); **or** **Root
     Directory** `frontend` · **Build** `npm run build` · **Output** `build`.
     If you see `index.html not found in ., … exiting`, the Output Directory is
     `.` with nothing built there — set it to `dist` (or `build`) as above.
     IPFS-safe (relative asset paths, query-string-only routing).
   - **Render Static Site** — Root Directory `frontend`, Build `npm run build`,
     Publish Directory `build`.
4. **Point the frontend at the backend**: `frontend/config.js` already defaults
   to `https://bethesdasearch-api.onrender.com`; edit that line, append
   `?api=https://…`, or use the in-app ⚙︎ setting. A green "✓ connected · N docs"
   confirms it. (The API URL resolves at runtime, so Dappling/Vercel build-time
   env vars aren't needed.)

> Use **one** deploy path. The Git integration above auto-deploys on push. The
> repo also includes a **manual** GitHub Actions workflow
> ([`.github/workflows/deploy-frontend.yml`](../.github/workflows/deploy-frontend.yml))
> as a CLI-based fallback (run from the Actions tab; needs `VERCEL_TOKEN`,
> `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` secrets). It does not run on push, so the
> two never double-deploy.

See [`../frontend/README.md`](../frontend/README.md) for details.

## Post-deploy operations

- **Re-index / add sources**: `flask engine ingest <source> -q "…" -n N`, or run
  it off-request on the worker: `from engine.tasks import ingest_source;
  ingest_source.delay("arxiv", query="cat:cs.RO", limit=1000)`.
- **Smoke test**: from the web service **Shell**, one command verifies the whole
  stack (init → ingest demo → search → answer, plus the live HTTP API):
  ```bash
  bash deploy/smoke.sh
  # or against the public URL:  bash deploy/smoke.sh https://bethesdasearch-api.onrender.com
  ```
  The in-process part is `flask engine smoke` (backend-agnostic; exits non-zero
  on failure — handy in CI too).
- **Health**: `GET /up/` (liveness) and `GET /api/v1/health` (engine + index
  status and document count).
- **Legacy book search** (`/legacy`) needs the original MariaDB dataset, which
  this Blueprint does not provision; it is inactive on this deployment.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Web up but search returns 503 | Elasticsearch not reachable — check `ELASTICSEARCH_HOST` and that the ES service is running. |
| Search returns 0 results | No data ingested yet — run `flask engine ingest …`. |
| Worker/collections error about `postgres://` | Old SQLAlchemy; the app auto-rewrites the scheme, but ensure you're on this version. |
| ES container restarts / OOM | Increase the plan or lower `ES_JAVA_OPTS` heap. |
| Build slow / image large | The engine ML deps (torch) are heavy; set `ENGINE_EMBEDDING_FALLBACK=true` and they become optional. |
