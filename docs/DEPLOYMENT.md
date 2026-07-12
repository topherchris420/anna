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

[`render.yaml`](../render.yaml) declares the entire stack. Render provisions all
of it from one file.

1. Push this repo to GitHub (already done for your fork).
2. In the [Render dashboard](https://dashboard.render.com): **New → Blueprint**,
   select this repository, and apply. Render creates:
   - `engineering-intelligence` — the web service (UI at `/`, API at `/api/v1`)
   - `engine-worker` — the Celery background indexing worker
   - `engine-elasticsearch` — a private Elasticsearch 8.5 service with a disk
   - `engine-postgres` — managed PostgreSQL (collections/bookmarks)
   - `engine-redis` — managed Redis (Celery broker)
3. Wait for the first deploy to go green (the image builds `deploy/Dockerfile`).
4. **Initialize the index and load data.** Open the web service → **Shell**:
   ```bash
   flask engine index-init          # create the hybrid ES index
   flask engine collections-init    # create the collections tables
   flask engine demo                # index a few offline sample docs
   # then ingest real data:
   flask engine ingest arxiv  -q "cat:eess.SY" -n 500
   flask engine ingest github -q "topic:rtos stars:>1000" -n 200
   flask engine ingest nasa   -q "propulsion" -n 300
   ```
5. Visit the web service URL. Search should work; before any ingestion the UI
   shows a friendly empty state.

Your site is the `engineering-intelligence` service's `onrender.com` URL (add a
custom domain in the dashboard).

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

1. Deploy the backend (Option A/B) and note its URL, e.g.
   `https://engineering-intelligence.onrender.com`.
2. **Allow the frontend origin in CORS.** On the backend, set:
   ```
   ENGINE_CORS_ORIGINS=https://your-app.vercel.app,https://your-app.on-dappling.network
   ```
   (Default is `*`, which works immediately but allows any origin.) On Render,
   add this as an env var on the `engineering-intelligence` service.
3. **Deploy the frontend** as its own project:
   - **Vercel**: import the repo, set **Root Directory = `frontend`**, framework
     *Other*, no build command, output `.`.
   - **Dappling**: connect the repo, base/publish directory `frontend`, static.
4. **Point the frontend at the backend**: edit `frontend/config.js`
   (`window.ENGINE_API_BASE = "https://…"`), or append `?api=https://…` to the
   URL, or use the in-app ⚙︎ setting. A green "✓ connected · N docs" confirms it.

To **auto-deploy** the frontend on every push, the repo includes a GitHub
Actions workflow ([`.github/workflows/deploy-frontend.yml`](../.github/workflows/deploy-frontend.yml)):
production on push to `main`, preview on PRs. Add the `VERCEL_TOKEN`,
`VERCEL_ORG_ID`, and `VERCEL_PROJECT_ID` repo secrets to enable it (it skips
gracefully until then).

See [`../frontend/README.md`](../frontend/README.md) for details.

## Post-deploy operations

- **Re-index / add sources**: `flask engine ingest <source> -q "…" -n N`, or run
  it off-request on the worker: `from engine.tasks import ingest_source;
  ingest_source.delay("arxiv", query="cat:cs.RO", limit=1000)`.
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
