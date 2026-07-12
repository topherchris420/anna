#!/bin/bash
# SessionStart hook: install the toolchain needed to run the Vers3Dynamics
# Engineering Intelligence engine tests and linters, then run the engine test
# suite as a startup smoke check.
#
# The engine tests are pure Python and do not need the full app stack (Flask,
# Celery, Elasticsearch, torch), so this installs a small, fast, project-pinned
# set of tools instead of the heavy runtime/ML dependencies.
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

# Test + lint toolchain (pinned to the project's dev versions) plus SQLAlchemy,
# which enables the collections tests instead of skipping them. Idempotent: pip
# is a no-op when these are already present, and the container is cached after
# the first successful run.
if ! python3 -m pip install --quiet --disable-pip-version-check \
      pytest==7.1.3 pytest-timeout==2.1.0 pytest-cov==3.0.0 \
      flake8==5.0.4 black==22.8.0 isort==5.10.1 \
      SQLAlchemy==1.4.41 > "$PIP_LOG" 2>&1; then
  echo "SessionStart hook: dependency install failed. See $PIP_LOG"
  tail -n 20 "$PIP_LOG" || true
  exit 0  # don't block session startup
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
