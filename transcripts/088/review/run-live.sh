#!/usr/bin/env bash
# Live independent-review evidence for finding 088.
#
#   ANTHROPIC_API_KEY=... OPENAI_API_KEY=... ./run-live.sh AGENTGATEWAY_BIN PYTHON...
#
# Each PYTHON must have openai installed; pass one per SDK version to test.
# Provider IDs and fingerprints are redacted inline. Keys are read from the
# environment and never written anywhere.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGW="$1"; shift
WORK="$(mktemp -d "${TMPDIR:-/tmp}/kairo088-live.XXXXXX")"
GW=""
cleanup() { [[ -n "$GW" ]] && kill "$GW" 2>/dev/null; wait 2>/dev/null; rm -rf "$WORK"; }
trap cleanup EXIT
sanitize() {
  sed -E \
    -e 's/"id":"msg_[^"]+"/"id":"msg_[REDACTED]"/g' \
    -e 's/"id":"toolu_[^"]+"/"id":"toolu_[REDACTED]"/g' \
    -e 's/"id":"chatcmpl-[^"]+"/"id":"chatcmpl-[REDACTED]"/g' \
    -e 's/"id":"call_[^"]+"/"id":"call_[REDACTED]"/g' \
    -e 's/"system_fingerprint":"[^"]+"/"system_fingerprint":"[REDACTED]"/g' \
    -e 's/"obfuscation":"[^"]*"/"obfuscation":"[REDACTED]"/g'
}
PROMPT='"messages":[{"role":"user","content":"What is the weather in San Francisco? Use the tool."}],"tools":[{"type":"function","function":{"name":"get_weather","description":"Get weather for a city","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}]'

cd "$WORK"
"$AGW" -f "$HERE/live-agentgateway-config.yaml" > gw.log 2>&1 &
GW=$!
for _ in $(seq 1 40); do (exec 3<>/dev/tcp/127.0.0.1/4100) 2>/dev/null && break; sleep 0.25; done

# 1. agentgateway -> live Anthropic, raw client-side bytes, five runs.
{
  echo "agentgateway $("$AGW" --version | tr -d ' \n')"
  echo "date $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  for i in 1 2 3 4 5; do
    curl -s -o "a$i.sse" -w '' http://127.0.0.1:4100/v1/chat/completions -H 'content-type: application/json' \
      -d "{\"model\":\"anthropic/claude-haiku-4-5-20251001\",\"max_tokens\":200,\"stream\":true,\"tool_choice\":\"required\",$PROMPT}"
    sanitize < "a$i.sse" > "$HERE/live-anthropic-$i.sse"
    echo "run $i: opening type:\"function\" $(grep -o '"type":"function"' "a$i.sse" | wc -l | tr -d ' '), continuation \"type\":null $(grep -o '"type":null' "a$i.sse" | wc -l | tr -d ' ')"
  done
} > "$HERE/live-anthropic-wire.txt"

# 2. The same call through each SDK's accumulating helper.
: > "$HERE/live-anthropic-sdk.txt"
for PY in "$@"; do
  "$PY" - >> "$HERE/live-anthropic-sdk.txt" 2>&1 <<'PY'
import traceback
import openai
from openai import OpenAI
c = OpenAI(base_url="http://127.0.0.1:4100/v1", api_key="unused")
body = dict(model="anthropic/claude-haiku-4-5-20251001", max_tokens=200, tool_choice="required",
            messages=[{"role": "user", "content": "What is the weather in San Francisco? Use the tool."}],
            tools=[{"type": "function", "function": {"name": "get_weather", "description": "Get weather for a city",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}])
for i in range(3):
    try:
        with c.chat.completions.stream(**body) as s:
            f = s.get_final_completion()
        print(f"openai {openai.__version__} run {i + 1}: OK {[t.function.arguments for t in f.choices[0].message.tool_calls or []]}")
    except BaseException as e:
        fr = traceback.extract_tb(e.__traceback__)[-1]
        print(f"openai {openai.__version__} run {i + 1}: RAISED {type(e).__name__} at {fr.filename.split('site-packages/')[-1]}:{fr.lineno}")
PY
done

# 3. Control: OpenAI direct, no gateway, same tool and prompt.
{
  echo "date $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  for i in 1 2 3; do
    curl -s -o "o$i.sse" https://api.openai.com/v1/chat/completions -H 'content-type: application/json' \
      -H "authorization: Bearer $OPENAI_API_KEY" \
      -d "{\"model\":\"gpt-4.1-mini\",\"max_tokens\":200,\"stream\":true,\"tool_choice\":\"required\",$PROMPT}"
    sanitize < "o$i.sse" > "$HERE/live-openai-direct-$i.sse"
    python3 - "o$i.sse" "$i" <<'PY'
import json, sys
tcs = [tc for l in open(sys.argv[1]) if l.startswith("data: {")
       for ch in json.loads(l[6:]).get("choices", [])
       for tc in ch.get("delta", {}).get("tool_calls") or []]
print(f"run {sys.argv[2]}: {len(tcs)} tool_call deltas; opening keys {sorted(tcs[0])}; "
      f"continuation key sets {sorted({tuple(sorted(t)) for t in tcs[1:]})}; "
      f"any explicit null type: {any('type' in t and t['type'] is None for t in tcs)}")
PY
  done
} > "$HERE/live-openai-direct.txt"
echo "done"
