#!/usr/bin/env bash
# Boot the fake Google upstream and a LiteLLM proxy, then run the finding 113 matrix.
#
# Usage: run.sh <venv_bin> <out_dir> [build_label]
#   env: KR113_MASTER_KEY, KR113_DATABASE_URL must be set.
set -euo pipefail
VENV="${1:-/Users/atharva/.cache/kr-hunt-1027/venv/bin}"
OUT="${2:-$(pwd)/wire}"
LABEL="${3:-main}"
PROXY_PORT="${PROXY_PORT:-4928}"
UPSTREAM_PORT="${UPSTREAM_PORT:-4927}"
RIG="$(cd "$(dirname "$0")" && pwd)"
SCRATCH="$(mktemp -d)"
CFG="$(mktemp).yaml"

: "${KR113_MASTER_KEY:=sk-master-113}"
: "${KR113_DATABASE_URL:=postgresql://atharva@localhost:5432/litellm_kairo}"

export KR113_MASTER_KEY
export KR113_DATABASE_URL
export GEMINI_API_BASE="http://127.0.0.1:${UPSTREAM_PORT}"
export GEMINI_API_KEY="fake-gemini-master-key"
export PYTHON_DOTENV_DISABLED="1"

cleanup() {
  kill "${UP_PID:-}" "${PROXY_PID:-}" 2>/dev/null || true
  pkill -f "litellm --config ${CFG}" 2>/dev/null || true
  rm -rf "$SCRATCH" "$CFG"
}
trap cleanup EXIT

"$VENV/python" "$RIG/fake_upstream.py" "$UPSTREAM_PORT" "$SCRATCH" &
UP_PID=$!

cp "$RIG/config.yaml" "$CFG"

mkdir -p "$OUT"
PATH="$VENV:$PATH" "$VENV/litellm" --config "$CFG" --port "$PROXY_PORT" >"$OUT/proxy.out.txt" 2>&1 &
PROXY_PID=$!

echo "Waiting for LiteLLM proxy on port ${PROXY_PORT}..."
for i in $(seq 1 60); do
  if curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PROXY_PORT}/health/readiness" 2>/dev/null | grep -q 200; then
    break
  fi
  sleep 1
done

KEY=$(curl -s "http://127.0.0.1:${PROXY_PORT}/key/generate" \
  -H "Authorization: Bearer ${KR113_MASTER_KEY}" -H 'content-type: application/json' \
  -d '{"models":["cheap-model"],"key_alias":"cheap-only-113","max_budget":1000.0}' \
  | "$VENV/python" -c 'import sys,json;print(json.load(sys.stdin)["key"])')
echo "Generated cheap-only key ${KEY:0:10}..."

"$VENV/python" "$RIG/drive.py" "http://127.0.0.1:${PROXY_PORT}" "$KEY" "$SCRATCH" "$OUT" 5 "$LABEL"

echo "Finding 113 run complete."
