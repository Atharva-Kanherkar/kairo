#!/bin/bash
# Start the issue 084 Codex rig: client tap -> LiteLLM 1.102.1 -> upstream tap -> OpenAI,
# plus a direct tap -> OpenAI for the no-gateway control.
# Requires OPENAI_API_KEY in the environment. It is passed to LiteLLM and the direct tap only.
set -eu
: "${OPENAI_API_KEY:?set OPENAI_API_KEY}"
RIG=${RIG:-/tmp/k084c}; PY=${PY:-/tmp/litellm-1.102.1/bin/python}; LITELLM=${LITELLM:-/tmp/litellm-1.102.1/bin/litellm}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$RIG/runs"; echo "$RIG/runs/setup" > "$RIG/runs/CURRENT"
"$PY" "$HERE/tap.py" --listen 9997 --target https://api.openai.com --out "$RIG/runs/upstream" > "$RIG/tap-up.log" 2>&1 &
"$PY" "$HERE/tap.py" --listen 9998 --target http://127.0.0.1:4010 --out "$RIG/runs/client" > "$RIG/tap-client.log" 2>&1 &
REAL_OPENAI_KEY=$OPENAI_API_KEY "$PY" "$HERE/tap.py" --listen 9999 --target https://api.openai.com --out "$RIG/runs/direct" --inject-key-env REAL_OPENAI_KEY > "$RIG/tap-direct.log" 2>&1 &
LITELLM_MASTER_KEY=sk-review-master OPENAI_API_BASE=http://127.0.0.1:9997 "$LITELLM" --config "$HERE/litellm.yaml" --host 127.0.0.1 --port 4010 > "$RIG/litellm.log" 2>&1 &
for _ in $(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:4010/health/liveliness && break; sleep 2; done
echo "rig ready"
