#!/bin/sh
# Free-tier container entrypoint: start gunicorn immediately, then initialize
# and seed the database in the background. Liveness must not depend on a cold or
# temporarily unavailable external database.
set -e

bootstrap_database() {
  # Keep retrying in the background so a database or network outage cannot
  # permanently strand a healthy web process in degraded mode. `set -e` is not
  # tripped by a failing command used as an `until` condition.
  echo "[entrypoint] initializing index + collections…"
  n=0
  retry_delay="${BOOTSTRAP_RETRY_DELAY:-3}"
  retry_max_delay="${BOOTSTRAP_RETRY_MAX_DELAY:-60}"
  until flask engine index-init && flask engine collections-init; do
    n=$((n + 1))
    echo "[entrypoint] database not ready, retrying in ${retry_delay}s (attempt $n)…"
    sleep "$retry_delay"
    if [ "$retry_delay" -lt "$retry_max_delay" ]; then
      retry_delay=$((retry_delay * 2))
      if [ "$retry_delay" -gt "$retry_max_delay" ]; then
        retry_delay="$retry_max_delay"
      fi
    fi
  done

  # Seed the offline demo documents (idempotent upsert by id). Best-effort: a
  # seeding hiccup must never take the web server down.
  echo "[entrypoint] seeding demo documents…"
  flask engine demo || echo "[entrypoint] demo seeding failed (continuing)"

  # Seed a real corpus after the bundled documents. The whole bootstrap already
  # runs in the background, so this process remains supervised by that job.
  if [ "${SEED_CORPUS:-true}" = "true" ]; then
    echo "[entrypoint] seeding real arXiv corpus in the background" \
         "(target=${SEED_CORPUS_TARGET:-300})…"
    flask engine seed-corpus --target "${SEED_CORPUS_TARGET:-300}" || \
      echo "[entrypoint] corpus seeding failed (continuing)"
  fi
}

# Start database work without delaying the liveness endpoint, then hand PID 1
# to gunicorn. Render injects $PORT; see config/gunicorn.py.
bootstrap_database &
echo "[entrypoint] starting web server…"
exec gunicorn -c python:config.gunicorn "allthethings.app:create_app()"
