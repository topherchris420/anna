#!/bin/bash
# SessionStart hook: install the toolchain needed to run the Anna engine
# tests and linters, then run the engine test suite as a startup smoke check.
#
# The engine tests are pure Python and do not need the full app stack (Celery,
# Elasticsearch, torch), so this installs a small, fast, project-pinned set of
# tools instead of the heavy runtime/ML dependencies.
set -euo pipefail

# Only run in Claude Code on the web (remote) sessions.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$PROJECT_DIR"

# Make `engine` and `allthethings` importable (mirrors the Docker image).
if [ -n "${CLAUDE_ENV_FILE:-}" ] && ! grep -q 'PYTHONPATH' "$CLAUDE_ENV_FILE" 2>/dev/null; then
  echo 'export PYTHONPATH="."' >> "$CLAUDE_ENV_FILE"
fi
export PYTHONPATH="."

PIP_LOG="/tmp/engine-hook-pip.log"
TEST_LOG="/tmp/engine-hook-tests.log"

install() {
  python3 -m pip install --quiet --disable-pip-version-check "$@" >> "$PIP_LOG" 2>&1
}

: > "$PIP_LOG"

# Test + lint toolchain (pinned to the project's dev versions). Idempotent: pip
# is a no-op when these are already present, and the container is cached after
# the first successful run.
TOOLCHAIN=(pytest==7.1.3 pytest-timeout==2.1.0 pytest-cov==3.0.0
           flake8==5.0.4 black==22.8.0 isort==5.10.1)
notes=()

# SQLAlchemy enables the collections tests instead of skipping them, but its
# pinned 1.4 release has no wheel for every interpreter (Python 3.13 has to
# build it from source, which fails under current setuptools). Without it the
# collections tests skip and everything else still runs, so it is not allowed
# to take the whole toolchain down with it.
if install "${TOOLCHAIN[@]}" SQLAlchemy==1.4.41; then
  :
elif install "${TOOLCHAIN[@]}"; then
  notes+=("SQLAlchemy==1.4.41 could not be installed for $(python3 --version 2>&1); collections tests will skip")
else
  echo "SessionStart hook: dependency install failed. See $PIP_LOG"
  tail -n 20 "$PIP_LOG" || true
  exit 0  # don't block session startup
fi

# Flask and Werkzeug, at the versions CI's smoke lane uses, make the API,
# CLI and server-page tests run instead of skipping. Best-effort: the engine
# itself does not need them.
if ! python3 -c 'import flask' 2>/dev/null && ! install Flask==2.2.2 Werkzeug==2.2.2; then
  notes+=("Flask==2.2.2 could not be installed; API and page tests will skip")
fi

# Startup smoke check: run the engine unit tests (pure Python, no infra).
# The project's pytest.ini/conftest target the full app, so we use a clean
# config and skip conftest for the engine-only suite.
if python3 -m pytest test/engine -c /dev/null --noconftest -q > "$TEST_LOG" 2>&1; then
  echo "SessionStart hook: engine deps ready; engine tests PASS — $(tail -n 1 "$TEST_LOG")"
else
  echo "SessionStart hook: engine deps ready; engine tests FAILED — see $TEST_LOG"
  tail -n 20 "$TEST_LOG" || true
fi
for note in "${notes[@]}"; do
  echo "SessionStart hook: $note. See $PIP_LOG"
done
