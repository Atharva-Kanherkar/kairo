#!/usr/bin/env bash
# Independent-review evidence for finding 088 that needs no credential.
#
#   ./run-keyless.sh AGENTGATEWAY_BIN PYTHON
#
# AGENTGATEWAY_BIN is the v1.5.0 release binary. PYTHON must have
# openai==3.19.2 and langchain-openai==1.6.6. npm must be on PATH; openai-node
# 7.23.0 is installed into a temporary directory. Outputs are written next to
# this script.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
T="$(cd "$HERE/.." && pwd)"
AGW="$1"
PY="$2"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/kairo088-review.XXXXXX")"
PIDS=()
cleanup() {
  for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null; done
  wait 2>/dev/null
  rm -rf "$WORK"
}
trap cleanup EXIT
wait_port() {
  local t=60
  while ((t-- > 0)); do
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return 0
    sleep 0.25
  done
  return 1
}
stage() { echo "$1" > "$WORK/canned.pointer"; }

cd "$WORK"
python3 "$T/consumer/capture_upstream.py" 9990 "$WORK/up.jsonl" "$T/upstream.sse" > mock.log 2>&1 &
PIDS+=($!)
"$AGW" -f "$T/consumer/agentgateway-config.yaml" > gw.log 2>&1 &
PIDS+=($!)
{ wait_port 9990 && wait_port 4003 && wait_port 4002; } || { echo "rig did not start" >&2; exit 2; }

# 1. Raw gateway bytes, five times, against the committed observed.sse.
stage "$T/upstream.sse"
BODY='{"model":"captured-model","max_tokens":64,"stream":true,"messages":[{"role":"user","content":"hi"}],"tools":[{"type":"function","function":{"name":"get_weather","description":"w","parameters":{"type":"object","properties":{"city":{"type":"string"}}}}}]}'
norm() { sed -E 's/"created":[0-9]+/"created":0/' "$1"; }
{
  echo "agentgateway $("$AGW" --version | tr -d ' \n')"
  echo "POST :4003/v1/chat/completions, stream:true, one tool, capture upstream serving upstream.sse"
  for i in 1 2 3 4 5; do
    curl -s -o "raw$i.sse" http://127.0.0.1:4003/v1/chat/completions \
      -H 'content-type: application/json' -d "$BODY"
    if cmp -s <(norm "raw$i.sse") <(norm "$T/observed.sse"); then
      echo "run $i: byte-identical to observed.sse apart from \"created\"; \"type\":null count $(grep -o '"type":null' "raw$i.sse" | wc -l | tr -d ' ')"
    else
      echo "run $i: DIFFERS from observed.sse"
    fi
  done
} > "$HERE/raw-capture-check.txt"
cp raw1.sse "$HERE/gateway-raw-v1.5.0.sse"

# 2. The request agentgateway forwarded to the Anthropic provider for that call.
tail -1 up.jsonl | python3 -c '
import json, sys
r = json.loads(sys.stdin.read())
print(json.dumps({"method": r["method"], "path": r["path"],
                  "header_names": sorted(r["headers"]), "body": r["body"]}, indent=2))
' > "$HERE/forwarded-request-v1.5.0.json"

# 3. LangChain ChatOpenAI, with and without response_format.
{
  echo "$("$PY" -c 'from importlib.metadata import version as v; print("langchain-openai", v("langchain-openai"), "openai", v("openai"))')"
  stage "$T/upstream.sse"
  for _ in 1 2 3; do "$PY" "$HERE/langchain_consumer.py" 4003 fmt; done
  "$PY" "$HERE/langchain_consumer.py" 4003 plain
  stage "$T/control-passthrough.sse"
  "$PY" "$HERE/langchain_consumer.py" 4002 fmt
} > "$HERE/langchain-outcomes.txt" 2>&1

# 4. openai-node against the same bytes, gateway not in the loop.
python3 "$T/consumer/isolate.py" 9997 agw_verbatim > iso.log 2>&1 &
PIDS+=($!)
wait_port 9997 || { echo "isolate server did not start" >&2; exit 2; }
mkdir node && cp "$HERE/node_consumer.mjs" node/ && (cd node && npm init -y > /dev/null && npm i -s openai@7.23.0 > /dev/null 2>&1)
{
  echo "openai-node $(grep -m1 '"version"' node/node_modules/openai/package.json | tr -dc '0-9.')"
  node node/node_consumer.mjs 9997
} > "$HERE/openai-node-outcomes.txt" 2>&1
echo "done"
