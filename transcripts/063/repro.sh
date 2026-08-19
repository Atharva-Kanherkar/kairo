#!/bin/bash
# Replay the 063 redirect leak locally. Canaries only. No provider keys.
# Usage: SWITCHYARD_BIN=/path/to/switchyard-server ./transcripts/063/repro.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BIN="${SWITCHYARD_BIN:-switchyard-server}"
DIR="$ROOT/transcripts/063"
export SY_HUNT_ANTH_KEY=CANARY_ANTHROPIC_API_KEY_ENV
export SY_HUNT_OA_KEY=CANARY_OPENAI_API_KEY_ENV

cleanup() {
  kill $(jobs -p) 2>/dev/null || true
}
trap cleanup EXIT

rm -f "$DIR"/repro-*-sink.jsonl "$DIR"/repro-*-origin.jsonl

python3 "$DIR/redirect_pair.py" sink 19321 "$DIR/repro-anth-sink.jsonl" &
python3 "$DIR/redirect_pair.py" origin 19320 "$DIR/repro-anth-origin.jsonl" \
  http://127.0.0.1:19321/v1/messages 307 &
python3 "$DIR/redirect_pair.py" sink 19331 "$DIR/repro-oa-sink.jsonl" &
python3 "$DIR/redirect_pair.py" origin 19330 "$DIR/repro-oa-origin.jsonl" \
  http://127.0.0.1:19331/v1/chat/completions 307 &
python3 "$DIR/redirect_pair.py" sink 19341 "$DIR/repro-goog-sink.jsonl" &
python3 "$DIR/redirect_pair.py" origin 19340 "$DIR/repro-goog-origin.jsonl" \
  http://127.0.0.1:19341/v1/chat/completions 307 &
sleep 0.3

"$BIN" --config "$DIR/sy-anth.toml" --host 127.0.0.1 -p 19322 >/dev/null 2>&1 &
"$BIN" --config "$DIR/sy-openai.toml" --host 127.0.0.1 -p 19332 >/dev/null 2>&1 &
"$BIN" --config "$DIR/sy-goog.toml" --host 127.0.0.1 -p 19342 >/dev/null 2>&1 &
sleep 0.8

echo "=== Anthropic api_key_env 307 (expect sink x-api-key canary) ==="
curl -sS -o /dev/null -w "http %{http_code}\n" http://127.0.0.1:19322/v1/messages \
  -H 'anthropic-version: 2023-06-01' -H 'content-type: application/json' \
  -d '{"model":"claude-hunt","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}'
python3 -c "import json; h={k.lower():v for k,v in json.loads(open('$DIR/repro-anth-sink.jsonl').read().splitlines()[0])['headers'].items()}; print('sink x-api-key', h.get('x-api-key'))"

echo "=== OpenAI Bearer 307 control (expect sink Authorization absent) ==="
curl -sS -o /dev/null -w "http %{http_code}\n" http://127.0.0.1:19332/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"gpt-hunt","messages":[{"role":"user","content":"hi"}]}'
python3 -c "import json; h={k.lower():v for k,v in json.loads(open('$DIR/repro-oa-sink.jsonl').read().splitlines()[0])['headers'].items()}; print('sink authorization', h.get('authorization'))"

echo "=== Gemini extra_headers x-goog-api-key 307 (expect sink goog canary, no Bearer) ==="
curl -sS -o /dev/null -w "http %{http_code}\n" http://127.0.0.1:19342/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"gemini-hunt","messages":[{"role":"user","content":"hi"}]}'
python3 -c "import json; h={k.lower():v for k,v in json.loads(open('$DIR/repro-goog-sink.jsonl').read().splitlines()[0])['headers'].items()}; print('sink authorization', h.get('authorization')); print('sink x-goog-api-key', h.get('x-goog-api-key'))"
