# Testing

Four lanes, from fastest to most complete. CI runs all of them
([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)).

| Lane | Command | Needs |
|---|---|---|
| Engine and API | `python -m pytest -q -c test/engine/pytest-smoke.ini --noconftest` | Python 3.10+, Flask, SQLAlchemy, pytest |
| Frontend runtime | `npm run test:frontend` | Node 20+ (no packages) |
| Free-tier entrypoint | `npm run test:deploy` | Node 20+, POSIX `sh` |
| End to end | `ANNA_E2E_API=http://localhost:8000 npm run test:e2e` | a running backend, Playwright + Chromium |

## End-to-end tests

[`test/e2e/workbench.e2e.test.js`](../test/e2e/workbench.e2e.test.js) serves
`frontend/` the way a static host does and drives it in Chromium against a
**real** backend — no mocks. Each test is a user journey:

| Test | Proves |
|---|---|
| landing | The workbench reaches the backend and says *Anna is online*; mode labels match the backend. |
| search | A query returns documents only the backend index holds (the Shadow Libraries directory is not in the Demo corpus); nonsense returns nothing. |
| deep-page link | A shared URL past the last page (or past the API's 50-page bound) lands on the last page with results, and the URL and pager say so. |
| evidence | A citation opens its exact source passage; *Why this result?* explains the ranking; *Details* loads the full record and related documents; the research record downloads. |
| compare | Two selected results are compared by `POST /api/v1/compare`. |
| collections | A saved document is still saved after a reload (same browser profile = same workspace), then is removed and the collection deleted through the UI. |
| backend failure mid-session | With the API cut off in the browser, a search reports *Anna's research backend isn't reachable* — no Demo results — and Retry restores Live. |
| cold backend | While health is held open (a sleeping host), a search waits and runs when the backend answers. |
| backend down | The unavailable state offers Retry, Diagnostics (with the endpoint and the failed check) and Switch to Demo; Demo then works and is labelled. |
| demo mode | Demo is chosen explicitly, never calls the search API, labels its results, and hands back to Live. |
| workbench chrome | The title-bar buttons, Ingestion Status (per-source counts) and Diagnostics do what they show. |

Outages are simulated by request interception in the browser, so the backend is
never stopped. Wait times are shortened through `window.ANNA_RUNTIME_TUNING`,
which the tests define before the page loads; production pages never set it.

### Run a backend for them

The tests need the offline corpus (`flask engine demo`), which the free-tier
entrypoint loads on boot. Either:

```bash
# Docker Compose (Elasticsearch path)
docker-compose up -d
./run flask engine index-init && ./run flask engine collections-init && ./run flask engine demo
```

or, without Docker, the same container entrypoint the free tier runs, against any
PostgreSQL with the `vector` extension:

```bash
export FLASK_APP=allthethings.app FLASK_SKIP_DOTENV=true PYTHONPATH=. PORT=8000 \
       SECRET_KEY=local-only WEB_CONCURRENCY=1 \
       ENGINE_BACKEND=postgres ENGINE_EMBEDDING_FALLBACK=true SEED_CORPUS=false \
       DATABASE_URL=postgresql://postgres:postgres@localhost:5432/anna
pip install -r requirements.txt
./deploy/entrypoint-free.sh        # serves on :8000, initialises and seeds in the background
```

Then:

```bash
npm install --no-save playwright@1.56.1 && npx playwright install chromium
ANNA_E2E_API=http://localhost:8000 npm run test:e2e
```

`ANNA_E2E_FRONTEND_DIR=dist` tests the built site (`npm run build`) instead of
`frontend/`.

## Checking a deployment by hand

Open the site in a private window (no saved endpoint, no workspace) and:

1. The status bar reads *Anna is online · N documents* — or *Waking Anna's
   research backend…*, which should turn into *Anna is online* within about a
   minute on a free host. **Help ▸ Diagnostics…** shows the endpoint in use and
   the last health check.
2. Search `kalman filter`: results, a source report with citations, and
   *term-hashing vectors* or a semantic model named under *Research record*.
3. **Details** on a result shows the full record and related documents;
   **Compare** two results; **☆ Save** one into a new collection and reload — it
   is still saved.
4. **Edit ▸ Switch to Demo Mode**: the badge says DEMO and the notice says it is
   not Anna's research index; **Switch to Live** returns.
