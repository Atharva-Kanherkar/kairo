#!/usr/bin/env bash
# Cold-start reproduction for kairo finding 091.
#
# agentgateway's OpenAI Chat Completions to Anthropic Messages STREAM
# translation re-opens a tool_use block it already closed, when the upstream
# interleaves non-empty text with a tool call's argument deltas. One upstream
# tool call becomes two client tool calls, at a reused block index, with the
# arguments split in half.
#
# Every rung prints PASS or FAIL and the script exits non-zero if the claim does
# not hold, so it is usable as a reviewer gate.
#
#   ./reproduce.sh [path-to-agentgateway-binary]
#
# Requires: python3, curl, and an agentgateway release binary (v1.5.0 =
# fe673247, or v1.6.0-alpha.2 = 02110e2a). No provider credential of any kind.
# pip install anthropic is optional; without it the SDK rung is skipped and the
# wire-level rungs still decide the claim.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGW="${1:-agentgateway}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/kairo091.XXXXXX")"
MOCK_PORT="${MOCK_PORT:-9990}"
GW_PORT=4200
FAILURES=0
PIDS=()

cleanup() {
  for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null; done
  wait 2>/dev/null
  rm -rf "$WORK"
}
trap cleanup EXIT

step() { printf '\n=== %s ===\n' "$1"; }
ok()   { printf '  PASS  %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
note() { printf '  ....  %s\n' "$1"; }
skip() { printf '  SKIP  %s\n' "$1"; }

wait_port() {
  local port="$1" tries=60
  while ((tries-- > 0)); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then exec 3>&- 2>/dev/null; return 0; fi
    sleep 0.25
  done
  return 1
}

# Refuse to run if a port is taken. A stale gateway answers on the same port, so
# every rung below would silently test the wrong process and a readiness probe
# cannot detect it.
assert_free() {
  local port="$1" what="$2"
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    exec 3>&- 2>/dev/null
    echo "  port $port is already in use by another process; stop it first." >&2
    echo "  a stale $what would answer there and this run would not test $AGW." >&2
    lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | sed 's/^/    /'
    exit 2
  fi
}

# Stage an upstream stream and prove the mock is serving THAT file before
# trusting any observation. Comparing the decoded SSE payloads, because the mock
# writes HTTP chunked framing and raw bytes are not comparable.
stage() {
  cp "$HERE/$1" "$WORK/canned"
  printf '%s' "$WORK/canned" > "$WORK/canned.pointer"
  local served staged
  served=$(curl -sS -m 20 -X POST "http://127.0.0.1:$MOCK_PORT/v1/chat/completions" \
    -H 'content-type: application/json' -d '{}' | grep '^data: ' | md5)
  staged=$(grep '^data: ' "$HERE/$1" | md5)
  [[ "$served" == "$staged" ]] || { echo "  ABORT: mock is not serving $1" >&2; exit 2; }
}

# One client request through the gateway. Writes the client's SSE to $WORK/out
# and echoes the HTTP status. Asserts the transfer completed: curl exit 18
# (incomplete read) is a failure, not a pass.
client_stream() {
  local out="$1"
  local status
  status=$(curl -sS -N -m 30 -o "$out" -w '%{http_code}' \
    -X POST "http://127.0.0.1:$GW_PORT/v1/messages" \
    -H 'content-type: application/json' -H 'anthropic-version: 2023-06-01' \
    -d @"$HERE/req-stream.json")
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "curl_exit_$rc"
    return
  fi
  [[ -s "$out" ]] || { echo "empty_body"; return; }
  echo "$status"
}

starts() { grep -c '^data: .*"type":"content_block_start"' "$1"; }

# ---------------------------------------------------------------- preflight
step "Preflight"
if [[ ! -x "$AGW" ]]; then
  echo "  agentgateway binary not found or not executable: $AGW" >&2
  echo "  download it with:" >&2
  echo "    curl -sL -o agentgateway https://github.com/agentgateway/agentgateway/releases/download/v1.5.0/agentgateway-darwin-arm64" >&2
  echo "    chmod +x agentgateway && ./reproduce.sh ./agentgateway" >&2
  exit 2
fi
note "agentgateway: $("$AGW" --version 2>/dev/null | tr -d ' \n' | head -c 160)"
command -v python3 >/dev/null || { echo "  python3 is required" >&2; exit 2; }
assert_free "$MOCK_PORT" "capture upstream"
assert_free "$GW_PORT" "agentgateway"

# ------------------------------------------------------- capture upstream
step "Start capture upstream on :$MOCK_PORT"
CANNED_POINTER="$WORK/canned.pointer" \
  python3 "$HERE/capture.py" "$MOCK_PORT" "$WORK/up.jsonl" > "$WORK/mock.log" 2>&1 &
PIDS+=($!)
wait_port "$MOCK_PORT" || { echo "  capture upstream did not start"; cat "$WORK/mock.log"; exit 2; }
ok "capture upstream recording every forwarded body"

# ------------------------------------------------------------------ gateway
step "Start agentgateway on :$GW_PORT"
sed "s/hostOverride: 127\.0\.0\.1:9990/hostOverride: 127.0.0.1:$MOCK_PORT/g" \
  "$HERE/agentgateway.yaml" > "$WORK/agentgateway.yaml"
"$AGW" -f "$WORK/agentgateway.yaml" > "$WORK/gw.log" 2>&1 &
GW_PID=$!
PIDS+=("$GW_PID")
wait_port "$GW_PORT" || { echo "  gateway did not bind :$GW_PORT"; tail -20 "$WORK/gw.log"; exit 2; }
kill -0 "$GW_PID" 2>/dev/null || { echo "  the gateway process exited" >&2; tail -20 "$WORK/gw.log"; exit 2; }
BOUND=$(lsof -nP -iTCP:"$GW_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)
if [[ -n "$BOUND" && "$BOUND" != "$GW_PID" ]]; then
  echo "  :$GW_PORT is served by pid $BOUND, not the binary under test ($GW_PID)" >&2
  exit 2
fi
ok "gateway listening, served by the binary under test"

# ------------------------------------------------ rung 0: the defect itself
step "Rung 0, FAILING PATH, non-empty text interleaved with tool arguments"
stage upstream-nonempty-text-mid-args.sse
CODE=$(client_stream "$WORK/observed.sse")
[[ "$CODE" == "200" ]] \
  && ok "client got HTTP 200 with a complete transfer" \
  || bad "client result was '$CODE', expected a clean 200"
N=$(starts "$WORK/observed.sse")
[[ "$N" == "3" ]] \
  && ok "client stream opens 3 content blocks for 1 upstream tool call" \
  || bad "expected 3 content_block_start events, saw $N"

# The load-bearing assertion: a block index is opened more than once.
DUP=$(grep '^data: .*"type":"content_block_start"' "$WORK/observed.sse" \
  | grep -o '"index":[0-9]*' | sort | uniq -d)
[[ -n "$DUP" ]] \
  && ok "a block index is opened twice: $DUP" \
  || bad "no reused block index, so the defect did not reproduce"

# And the tool call itself is duplicated at the client.
TOOL_STARTS=$(grep -c '"type":"tool_use"' "$WORK/observed.sse")
[[ "$TOOL_STARTS" -ge 2 ]] \
  && ok "the same tool call is emitted as $TOOL_STARTS tool_use blocks" \
  || bad "expected the tool call to be duplicated, saw $TOOL_STARTS tool_use blocks"

# The arguments are split, so each half is independently invalid JSON.
FRAGS=$(grep -o '"partial_json":"[^"]*"' "$WORK/observed.sse" | sed 's/.*partial_json":"//;s/"$//')
echo "$FRAGS" | grep -q . && note "argument fragments delivered: $(echo "$FRAGS" | tr '\n' ' ')"
WHOLE=$(printf '%s' "$FRAGS")
if [[ -n "$WHOLE" ]] && ! printf '%s' "$WHOLE" | python3 -c 'import json,sys; json.loads(sys.stdin.read())' 2>/dev/null; then
  ok "the two halves do not concatenate into valid JSON, so neither call can run"
else
  note "concatenated fragments parse as JSON; a client may repair this by luck"
fi

# ------------------------------- rung 1: the discrimination, empty vs non-empty
step "Rung 1, CONTROL, the empty-content-delta case fixed by #2147/#2148"
stage upstream-empty-text-delta.sse
CODE2=$(client_stream "$WORK/empty.sse")
[[ "$CODE2" == "200" ]] \
  && ok "control client got HTTP 200 with a complete transfer" \
  || bad "control client result was '$CODE2'"
M=$(starts "$WORK/empty.sse")
[[ "$M" == "1" ]] \
  && ok "control opens 1 content block: the empty delta is a no-op, as upstream intended" \
  || bad "control opened $M blocks, expected 1"
DUP2=$(grep '^data: .*"type":"content_block_start"' "$WORK/empty.sse" \
  | grep -o '"index":[0-9]*' | sort | uniq -d)
[[ -z "$DUP2" ]] \
  && ok "control reuses no block index" \
  || bad "control also reused an index ($DUP2), so the two cases are not distinct"

# ------------------------------------- rung 2: buffered path on same process
step "Rung 2, CONTROL, the same interleaving in a buffered (stream:false) turn"
python3 - "$WORK" <<'PY'
import json, sys, os
work = sys.argv[1]
buf = {
    "id": "c", "object": "chat.completion", "created": 0, "model": "m",
    "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": " and also ",
        "tool_calls": [{"id": "call_a", "type": "function", "function": {
            "name": "get_weather", "arguments": '{"city":"sf"}'}}]}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}
open(os.path.join(work, "buffered.json"), "w").write(json.dumps(buf))
PY
cp "$WORK/buffered.json" "$WORK/canned"
printf '%s' "$WORK/canned" > "$WORK/canned.pointer"
BUF=$(curl -sS -X POST "http://127.0.0.1:$GW_PORT/v1/messages" \
  -H 'content-type: application/json' -H 'anthropic-version: 2023-06-01' \
  -d "$(python3 -c 'import json;d=json.load(open("'"$HERE"'/req-stream.json"));d.pop("stream",None);print(json.dumps(d))')")
echo "$BUF" > "$WORK/buffered-client.json"
NTOOL=$(printf '%s' "$BUF" | grep -o '"type": *"tool_use"' | wc -l | tr -d ' ')
[[ "$NTOOL" == "1" ]] \
  && ok "buffered turn yields exactly 1 tool_use block: only streaming is affected" \
  || bad "buffered turn yielded $NTOOL tool_use blocks, which contradicts the writeup"
printf '%s' "$BUF" | grep -q '"city": *"sf"' \
  && ok "buffered turn delivers the complete, valid arguments" \
  || bad "buffered turn lost or mangled the arguments"

# -------------------------------------------------------- rung 3: SDK effect
step "Rung 3, consumer boundary, official anthropic SDK accumulating stream"
if python3 -c 'import anthropic' 2>/dev/null; then
  note "anthropic $(python3 -c 'import anthropic;print(anthropic.__version__)')"
  SDKOUT=$(RAW_SSE="$WORK/observed.sse" python3 "$HERE/sdk_probe.py" "$GW_PORT" "observed" 2>&1)
  echo "$SDKOUT" | sed 's/^/      /'
  echo "$SDKOUT" | grep -q "RAISED" \
    && note "the SDK raised rather than silently duplicating" \
    || ok "the SDK accepts the stream without raising"
  COUNT=$(echo "$SDKOUT" | grep -c 'block: tool_use')
  [[ "$COUNT" == "2" ]] \
    && ok "the SDK reports 2 tool_use blocks for 1 upstream tool call, so the agent dispatches it twice" \
    || bad "the SDK reported $COUNT tool_use blocks, expected 2"
else
  skip "anthropic SDK not installed; pip install anthropic to run this rung"
fi

# ---------------------------------------------------------- rung 4: N of N
step "Rung 4, determinism, 10 runs of the failing path"
# Rung 2 left a buffered JSON body staged. Restage the failing stream, or these
# runs measure the buffered path instead of the streaming one.
stage upstream-nonempty-text-mid-args.sse
HITS=0
BADTRANSFER=0
for _ in $(seq 1 10); do
  R=$(client_stream "$WORK/run.sse")
  [[ "$R" == "200" ]] || BADTRANSFER=$((BADTRANSFER + 1))
  if grep -q '"type":"content_block_start","index":0' "$WORK/run.sse" \
     && [[ "$(starts "$WORK/run.sse")" == "3" ]]; then
    HITS=$((HITS + 1))
  fi
done
[[ "$BADTRANSFER" -eq 0 ]] \
  && ok "10 of 10 runs completed the transfer" \
  || bad "$BADTRANSFER of 10 runs had an incomplete transfer, which is not a pass"
[[ "$HITS" -eq 10 ]] \
  && ok "the duplicated block reproduced in 10 of 10 runs" \
  || bad "reproduced in $HITS of 10 runs, expected 10"

# ---------------------------------------------------- rung 5: frozen checker
step "Rung 5, frozen invariant via the harness"
REPO="$(cd "$HERE/../../.." && pwd)"
if (cd "$REPO" && cargo test -p kairo --test conformance -- \
      agentgateway_reopens_tool_use_block_after_text_interleaves \
      agentgateway_empty_text_delta_between_arguments_is_conformant 2>&1 | grep -q "2 passed"); then
  ok "both conformance assertions pass against the frozen bytes"
else
  bad "conformance assertions did not both pass"
fi
if (cd "$REPO" && cargo test -p kairo --test adversarial 2>&1 | grep -q "10 passed"); then
  ok "all 10 adversarial self-tests pass, so the checker can actually fail"
else
  bad "adversarial self-tests did not all pass"
fi

# ------------------------------------------------------------------ verdict
step "Verdict"
if [[ "$FAILURES" -eq 0 ]]; then
  echo "  REPRODUCED. Every rung held; the claim stands."
  exit 0
fi
echo "  NOT REPRODUCED CLEANLY. $FAILURES rung(s) failed; see above."
exit 1
