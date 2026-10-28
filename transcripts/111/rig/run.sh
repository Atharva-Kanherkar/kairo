#!/usr/bin/env bash
# Boot the fake upstream and a LiteLLM proxy from a pinned source tree, then run
# the finding 111 matrix. All ports and the source tree are arguments so the rig
# is reproducible on any checkout.
#
# Usage: run.sh <litellm_src> <venv_bin> <out_dir> [build_label]
#   env: KR111_MASTER_KEY, KR111_DATABASE_URL must be set.
#   env: KR111_BUDGET_CAPTURE=1 also runs the $1 budget-drain stage into
#        <out_dir>/budget/ (six generateContent calls on a fresh $1 key).
#
# Note: the spend dump needs psycopg2 in <venv>. Install it with
#   uv pip install --python <venv>/python psycopg2-binary
# so run.sh produces spend.json itself instead of skipping the dump.
set -euo pipefail
SRC="$1"; VENV="$2"; OUT="$3"; LABEL="${4:-main}"
PROXY_PORT="${PROXY_PORT:-4028}"
UPSTREAM_PORT="${UPSTREAM_PORT:-4190}"
RIG="$(cd "$(dirname "$0")" && pwd)"
SCRATCH="$(mktemp -d)"
CFG="$(mktemp).yaml"
: "${KR111_MASTER_KEY:?set KR111_MASTER_KEY}"
: "${KR111_DATABASE_URL:?set KR111_DATABASE_URL}"

cleanup() {
  # Kill the launched pids and, because the proxy spawns uvicorn under a
  # subshell, also any litellm bound to our config so the port is released.
  kill "${UP_PID:-}" "${PROXY_PID:-}" 2>/dev/null || true
  pkill -f "litellm --config ${CFG}" 2>/dev/null || true
}
trap cleanup EXIT

"$VENV/python" "$RIG/fake_upstream.py" "$UPSTREAM_PORT" "$SCRATCH" &
UP_PID=$!

sed "s#UPSTREAM_BASE#http://127.0.0.1:${UPSTREAM_PORT}/v1#" "$RIG/config.yaml" > "$CFG"

( cd "$SRC" && PATH="$VENV:$PATH" "$VENV/litellm" --config "$CFG" --port "$PROXY_PORT" ) >"$OUT/proxy.out.txt" 2>&1 &
PROXY_PID=$!

for i in $(seq 1 120); do
  if curl -s -o /dev/null -w '%{http_code}' "localhost:${PROXY_PORT}/health/readiness" 2>/dev/null | grep -q 200; then break; fi
  sleep 3
done
curl -s -o /dev/null -w 'proxy=%{http_code}\n' "localhost:${PROXY_PORT}/health/readiness"

KEY=$(curl -s "localhost:${PROXY_PORT}/key/generate" \
  -H "Authorization: Bearer ${KR111_MASTER_KEY}" -H 'content-type: application/json' \
  -d '{"models":["cheap-model"],"key_alias":"cheap-only-111","max_budget":1000.0}' \
  | "$VENV/python" -c 'import sys,json;print(json.load(sys.stdin)["key"])')
echo "generated cheap-only key ${KEY:0:6}..."

"$VENV/python" "$RIG/drive.py" "http://localhost:${PROXY_PORT}" "$KEY" "$SCRATCH" "$OUT" 5 "$LABEL"

# Optional $1 budget-drain stage: a fresh key scoped to cheap-model with a $1
# budget makes six generateContent calls naming premium-model in the URL path.
if [ "${KR111_BUDGET_CAPTURE:-0}" = "1" ]; then
  echo "--- budget drain (one-dollar key, 6 generateContent bypass calls) ---"
  BKEY=$(curl -s "localhost:${PROXY_PORT}/key/generate" \
    -H "Authorization: Bearer ${KR111_MASTER_KEY}" -H 'content-type: application/json' \
    -d '{"models":["cheap-model"],"key_alias":"cheap-only-budget1","max_budget":1.0}' \
    | "$VENV/python" -c 'import sys,json;print(json.load(sys.stdin)["key"])')
  mkdir -p "$OUT/budget"
  for n in $(seq 1 6); do
    curl -s "localhost:${PROXY_PORT}/v1beta/models/premium-model:generateContent" \
      -H "x-goog-api-key: ${BKEY}" -H 'content-type: application/json' \
      -d '{"model":"cheap-model","contents":[{"role":"user","parts":[{"text":"hi"}]}]}' \
      -o "$OUT/budget/resp-${n}.json" -w '%{http_code}' > "$OUT/budget/status-${n}.txt"
    echo "  call ${n}: $(cat "$OUT/budget/status-${n}.txt")"
    sleep 1
  done
fi

# LiteLLM writes spend rows asynchronously; wait for the queue to flush so the
# dump captures the final matrix and budget calls.
echo "--- waiting for spend log flush ---"
sleep 20
echo "--- spend log ---"
BUDGET_ALIAS=cheap-only-budget1 "$VENV/python" - "$KR111_DATABASE_URL" "$OUT" <<'PY'
import sys, json, os
url, out = sys.argv[1], sys.argv[2]
try:
    import psycopg2  # noqa
except Exception:
    print("psycopg2 not available; install psycopg2-binary so run.sh dumps spend")
    raise SystemExit(0)
import psycopg2
c = psycopg2.connect(url); cur = c.cursor()
cols = ["model", "model_group", "api_key", "status", "spend", "total_tokens", "startTime"]
cur.execute(
    'select model, model_group, api_key, status, spend, total_tokens, "startTime", metadata'
    ' from "LiteLLM_SpendLogs" order by "startTime"'
)
rows, budget_rows = [], []
budget_alias = os.environ.get("BUDGET_ALIAS", "")
for model, group, api_key, status, spend, tokens, start, meta in cur.fetchall():
    alias = (meta or {}).get("user_api_key_alias") if isinstance(meta, dict) else None
    record = [str(model), str(group), str(api_key), str(status), str(spend), str(tokens), str(start)]
    (budget_rows if alias == budget_alias else rows).append(record)
json.dump({"columns": cols, "note": "api_key is LiteLLM's token hash, not a secret",
           "rows": rows}, open(out + "/spend.json", "w"), indent=2)
print("wrote", out + "/spend.json", "rows:", len(rows))
if budget_rows:
    json.dump({"columns": cols, "key_alias": budget_alias, "rows": budget_rows},
              open(out + "/budget/spend.json", "w"), indent=2)
    print("wrote", out + "/budget/spend.json", "rows:", len(budget_rows))
PY
