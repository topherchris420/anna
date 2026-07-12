#!/bin/sh
# Free-tier container entrypoint: initialize the database and seed demo data on
# boot, then hand off to gunicorn. This removes the need to run any commands in
# the Render dashboard Shell.
set -e

# 1) Database setup. Wrapped in a bounded retry so a cold database on the very
#    first deploy (web container boots before Postgres accepts connections)
#    does not crash-loop the service. `set -e` is not tripped by a failing
#    command used as an `until` condition.
echo "[entrypoint] initializing index + collections…"
n=0
until flask engine index-init && flask engine collections-init; do
  n=$((n + 1))
  if [ "$n" -ge 20 ]; then
    echo "[entrypoint] database not ready after ~60s — starting server anyway"
    break
  fi
  echo "[entrypoint] database not ready, retrying in 3s ($n/20)…"
  sleep 3
done

# 2) Seed the offline demo documents (idempotent upsert by id). Best-effort:
#    a seeding hiccup must never take the web server down.
echo "[entrypoint] seeding demo documents…"
flask engine demo || echo "[entrypoint] demo seeding failed (continuing)"

# 3) Seed a real corpus of a few hundred arXiv papers, in the BACKGROUND so it
#    never delays boot / the health check. It is idempotent (skips once the
#    corpus meets the target) and resumable across cold starts, and arXiv needs
#    no API key. Disable with SEED_CORPUS=false; tune size with
#    SEED_CORPUS_TARGET. The backgrounded job survives the exec below because it
#    is a separate, already-forked process.
if [ "${SEED_CORPUS:-true}" = "true" ]; then
  echo "[entrypoint] seeding real arXiv corpus in the background" \
       "(target=${SEED_CORPUS_TARGET:-300})…"
  flask engine seed-corpus --target "${SEED_CORPUS_TARGET:-300}" &
fi

# 4) Hand off to the web server. gunicorn binds 0.0.0.0:$PORT (default 8000;
#    Render injects $PORT). See config/gunicorn.py.
echo "[entrypoint] starting web server…"
exec gunicorn -c python:config.gunicorn "allthethings.app:create_app()"
