# Production image for hosting the Vers3Dynamics Engineering Intelligence
# platform on a Docker host (Render, Fly.io, Railway, a VPS, ...).
#
# Unlike the dev Dockerfile at the repo root, this is a single Python stage:
# the modern UI ships self-contained CSS, so there is no Node/asset build step.
# gunicorn binds to $PORT (config/gunicorn.py), which these platforms provide.
FROM python:3.10.5-slim-bullseye

WORKDIR /app

# build-essential + libpq-dev are needed to build psycopg2 (PostgreSQL driver);
# curl is handy for container health checks.
RUN apt-get update \
  && apt-get install -y --no-install-recommends build-essential curl libpq-dev \
  && rm -rf /var/lib/apt/lists/* /usr/share/doc /usr/share/man \
  && apt-get clean

ENV FLASK_APP="allthethings.app" \
    FLASK_SKIP_DOTENV="true" \
    PYTHONUNBUFFERED="true" \
    PYTHONDONTWRITEBYTECODE="true" \
    PYTHONPATH="." \
    PIP_NO_CACHE_DIR="1" \
    PORT="8000"

# Install Python dependencies first for better layer caching.
COPY requirements.txt requirements-engine.txt ./
RUN pip install --no-warn-script-location -r requirements.txt \
  # Engine ML/PDF deps are best-effort: the platform falls back gracefully if
  # (e.g.) the torch wheel is unavailable, so a failure here must not break the
  # image build.
  && (pip install --no-warn-script-location -r requirements-engine.txt \
      || echo "WARN: engine deps failed to install; using embedding fallback.")

# The legacy entrypoint expects a /app/public directory to exist.
RUN mkdir -p /app/public

COPY . .

EXPOSE 8000

# Web process. Worker services override this with a celery command.
CMD ["gunicorn", "-c", "python:config.gunicorn", "allthethings.app:create_app()"]
