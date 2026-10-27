#!/usr/bin/env bash
# Boot the fake upstream and a LiteLLM proxy from a pinned source tree, then run
# the finding 111 matrix. All ports and the source tree are arguments so the rig
# is reproducible on any checkout.
#
# Usage: run.sh <litellm_src> <venv_bin> <out_dir> [build_label]
#   env: KR111_MASTER_KEY, KR111_DATABASE_URL must be set.
set -euo pipefail
SRC="$1"; VENV="$2"; OUT="$3"; LABEL="${4:-main}"
PROXY_PORT="${PROXY_PORT:-4028}"
UPSTREAM_PORT="${UPSTREAM_PORT:-4190}"
RIG="$(cd "$(dirname "$0")" && pwd)"
SCRATCH="$(mktemp -d)"
: "${KR111_MASTER_KEY:?set KR111_MASTER_KEY}"
: "${KR111_DATABASE_URL:?set KR111_DATABASE_URL}"

cleanup() { kill "${UP_PID:-}" "${PROXY_PID:-}" 2>/dev/null || true; }
trap cleanup EXIT

"$VENV/python" "$RIG/fake_upstream.py" "$UPSTREAM_PORT" "$SCRATCH" &
UP_PID=$!

CFG="$(mktemp).yaml"
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

echo "--- spend log ---"
"$VENV/python" - "$KR111_DATABASE_URL" "$OUT" <<'PY'
import sys, json
url, out = sys.argv[1], sys.argv[2]
try:
    import psycopg2  # noqa
except Exception:
    print("psycopg2 not available; skipping spend dump"); raise SystemExit(0)
import psycopg2
c = psycopg2.connect(url); cur = c.cursor()
cur.execute('select model, model_group, spend, total_tokens, "startTime" from "LiteLLM_SpendLogs" order by "startTime"')
rows = [[str(x) for x in r] for r in cur.fetchall()]
json.dump({"columns":["model","model_group","spend","total_tokens","startTime"],"rows":rows}, open(out+"/spend.json","w"), indent=2)
print("wrote", out+"/spend.json", "rows:", len(rows))
PY
