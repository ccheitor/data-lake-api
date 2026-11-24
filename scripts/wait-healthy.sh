#!/usr/bin/env bash
# Wait until GET /health reports every dependency ok (Trino takes ~30-60s to start).
set -euo pipefail
URL="${BASE_URL:-http://localhost:8000}/health"
for i in $(seq 1 60); do
  if curl -sf "$URL" >/dev/null 2>&1; then echo "API healthy: $(curl -s "$URL")"; exit 0; fi
  sleep 5
done
echo "API not healthy after 5 minutes; check: docker compose logs" >&2; exit 1
