#!/usr/bin/env bash
# End-to-end smoke test for the Engineering Intelligence backend.
#
# Run it from the Render web-service Shell (or any environment where the app is
# installed). One command confirms: index init, ingest, and the live HTTP API.
#
#   bash deploy/smoke.sh                                  # http://localhost:$PORT
#   bash deploy/smoke.sh https://bethesdasearch.onrender.com
#   SMOKE_QUERY="risc-v vector" bash deploy/smoke.sh
#
# The authoritative check is the in-process `flask engine smoke` (same code path
# the REST API uses); the HTTP checks additionally prove the live endpoints and
# only WARN if the server isn't reachable from where you run this.
set -uo pipefail

BASE="${1:-http://localhost:${PORT:-8000}}"
QUERY="${SMOKE_QUERY:-circular buffer dma}"
rc=0

hr() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

hr "Setup: init index + collections (idempotent)"
flask engine index-init && flask engine collections-init || rc=1

hr "In-process end-to-end: flask engine smoke"
flask engine smoke -q "$QUERY" || rc=1

hr "HTTP GET ${BASE}/api/v1/health"
if health=$(curl -fsS --max-time 20 "${BASE}/api/v1/health" 2>/dev/null); then
  echo "$health" | python3 -m json.tool
else
  echo "WARN: could not reach ${BASE} — run this in the web service Shell, or pass the public URL."
fi

hr "HTTP GET ${BASE}/api/v1/search"
q=$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))' "$QUERY")
if s=$(curl -fsS --max-time 30 "${BASE}/api/v1/search?q=${q}&per_page=5" 2>/dev/null); then
  echo "$s" | python3 -c '
import sys, json
d = json.load(sys.stdin)
hits = d.get("hits", [])
print("hits:", len(hits), "total:", d.get("total"), "took_ms:", d.get("took_ms"))
for h in hits[:5]:
    doc = h["document"]
    print("  -", doc["source"] + ":", doc["title"][:70])
' || echo "WARN: could not parse search response"
else
  echo "WARN: /api/v1/search not reachable at ${BASE}"
fi

hr "HTTP POST ${BASE}/api/v1/summarize"
body=$(python3 -c 'import json,sys; print(json.dumps({"q": sys.argv[1]}))' "$QUERY")
if a=$(curl -fsS --max-time 30 -X POST "${BASE}/api/v1/summarize" \
         -H 'Content-Type: application/json' -d "$body" 2>/dev/null); then
  echo "$a" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("answer:", (d.get("answer") or "(none)")[:200])'
else
  echo "WARN: /api/v1/summarize not reachable at ${BASE}"
fi

hr "Result"
if [ "$rc" = "0" ]; then
  echo "SMOKE OK ✅  (HTTP 'WARN' lines above don't fail the run.)"
else
  echo "SMOKE FAILED ❌"
fi
exit "$rc"
