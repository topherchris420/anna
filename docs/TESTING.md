# Testing

Five lanes, from fastest to most complete. CI runs all of them
([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)).

| Lane | Command | Needs |
|---|---|---|
| Engine and API | `python -m pytest -q -c test/engine/pytest-smoke.ini --noconftest` | Python 3.10+, Flask, SQLAlchemy, pytest |
| Frontend runtime | `npm run test:frontend` | Node 20+ (no packages) |
| Free-tier entrypoint | `npm run test:deploy` | Node 20+, POSIX `sh` |
| Relevance | `ANNA_TEST_DATABASE_URL=… ANNA_TEST_ELASTICSEARCH=… python -m pytest -c test/engine/pytest-smoke.ini --noconftest test/engine/test_relevance_live.py` | Postgres with pgvector and/or Elasticsearch 8; `psycopg2`, `elasticsearch` |
| End to end | `ANNA_E2E_API=http://localhost:8000 npm run test:e2e` | a running backend, Playwright + Chromium |

The engine lane also keeps the two browser twins in step by executing them
under Node: `test_records.py` compares canonical JSON and fingerprints with
`frontend/evidence.js`, and `test_citations.py` compares BibTeX keys, entries
and references with `frontend/citations.js` over a fixture of awkward
documents. `test_web_pages.py` holds the server-rendered pages to the
workbench's contract — an unreachable index renders as a 503 outage notice,
never as "no results"; relevance, mode labels and rank explanations read the
same; every document page cites its document.

## Relevance

[`test/engine/test_relevance_live.py`](../test/engine/test_relevance_live.py)
is the one engine module that runs the real SQL and query DSL. Name a backend
and it loads the judged corpus of [`test/relevance`](../test/relevance/README.md)
into a scratch index or table (`anna_relevance_test`, dropped afterwards), then
holds that backend to quality floors on the 47 judged queries (nDCG@10, MRR,
P@5, noise on unanswerable queries) and to the lexical behaviours the product
promises: a plain-English question finds its paper, gibberish and stop words
find nothing, quotes and `-exclusions` work, every hit names the terms it
carries, hybrid on term-hashing vectors runs lexically. Without
`ANNA_TEST_DATABASE_URL` / `ANNA_TEST_ELASTICSEARCH` it skips.

```bash
ANNA_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/anna \
ANNA_TEST_ELASTICSEARCH=http://localhost:9200 \
python -m pytest -c test/engine/pytest-smoke.ini --noconftest test/engine/test_relevance_live.py
```

To see the full per-topic report rather than pass/fail, or to measure a change
before proposing it: `./run flask engine evaluate --load-corpus --index anna_eval`.

## End-to-end tests

[`test/e2e/workbench.e2e.test.js`](../test/e2e/workbench.e2e.test.js) serves
`frontend/` the way a static host does and drives it in Chromium against a
**real** backend — no mocks. Each test is a user journey:

| Test | Proves |
|---|---|
| landing | The workbench reaches the backend and says *Anna is online*; mode labels match the backend. |
| search | A query returns documents only the backend index holds (the Shadow Libraries directory is not in the Demo corpus); nonsense returns nothing. |
| deep-page link | A shared URL past the last page (or past the API's 50-page bound) lands on the last page with results, and the URL and pager say so. |
| evidence | A citation opens its exact source passage; *Why this result?* explains the ranking; *Details* loads the full record and related documents and cites it (the BibTeX on screen equals what `GET /api/v1/document/<id>/citation` serves); the research record and the `.bib` bibliography download. |
| compare | Two selected results are compared by `POST /api/v1/compare`. |
| collections | A saved document is still saved after a reload (same browser profile = same workspace), then is removed and the collection deleted through the UI. |
| backend failure mid-session | With the API cut off in the browser, a search reports *Anna's research backend isn't reachable* — no Demo results — and Retry restores Live. |
| cold backend | While health is held open (a sleeping host), a search waits and runs when the backend answers. |
| backend down | The unavailable state offers Retry, Diagnostics (with the endpoint and the failed check) and Switch to Demo; Demo then works and is labelled. |
| demo mode | Demo is chosen explicitly, never calls the search API, labels its results, cites a bundled document offline, and hands back to Live. |
| workbench chrome | The title-bar buttons, Ingestion Status (per-source counts) and Diagnostics do what they show. |
| spelling | A misspelled word ("circluar buffer") is searched corrected and the notice says so with the corrected word marked; **Search instead for** re-runs exactly what was typed (`spell=0` on the request and in the URL); a new query is corrected again. |
| phones | At 320 and 390 px, the workbench with results and every page the backend serves itself (home, search, a document, collections, about, compare) fit the screen: nothing scrolls sideways and the phone never zooms out to fit a wider layout. |

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
