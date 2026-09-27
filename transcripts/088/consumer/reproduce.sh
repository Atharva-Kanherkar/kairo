#!/usr/bin/env bash
# Cold-start reproduction for kairo finding 088.
#
# Brings up a capture upstream and agentgateway v1.5.0 from nothing, then walks
# the differential ladder. Every rung prints PASS or FAIL and the script exits
# non-zero if the claim does not hold, so it is usable as a reviewer gate.
#
#   ./reproduce.sh [path-to-agentgateway-binary]
#
# Requires: python3 with `openai` installed, and the agentgateway v1.5.0
# darwin-arm64 release binary. No provider credential of any kind.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGW="${1:-agentgateway}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/kairo088.XXXXXX")"
MOCK_PORT=9990
CONV_PORT=4003      # openai ingress -> anthropic provider  (the conversion)
CTRL_PORT=4002      # openai ingress -> openai provider     (passthrough control)
ISO_PORT=9997
FAILURES=0
PIDS=()

cleanup() {
  for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null; done
  wait 2>/dev/null
  rm -rf "$WORK"
}
trap cleanup EXIT

step()  { printf '\n=== %s ===\n' "$1"; }
ok()    { printf '  PASS  %s\n' "$1"; }
bad()   { printf '  FAIL  %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
note()  { printf '  ....  %s\n' "$1"; }

wait_port() {
  local port="$1" tries=60
  while ((tries-- > 0)); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then exec 3>&- 2>/dev/null; return 0; fi
    sleep 0.25
  done
  return 1
}

stage() { echo "$1" > "$WORK/canned.pointer"; }

# ---------------------------------------------------------------- preflight
step "Preflight"
command -v "$AGW" >/dev/null 2>&1 || { AGW="$AGW"; }
if [[ ! -x "$AGW" ]]; then
  echo "  agentgateway binary not found or not executable: $AGW" >&2
  echo "  download it with:" >&2
  echo "    curl -sL -o agentgateway https://github.com/agentgateway/agentgateway/releases/download/v1.5.0/agentgateway-darwin-arm64" >&2
  echo "    chmod +x agentgateway && ./reproduce.sh ./agentgateway" >&2
  exit 2
fi
note "agentgateway: $("$AGW" --version 2>/dev/null | tr -d ' \n' | head -c 200)"
python3 -c 'import openai,sys; print("openai-python", openai.__version__)' || {
  echo "  openai is not installed. pip install openai==2.48.0" >&2; exit 2; }

# ------------------------------------------------------- capture upstream
step "Start capture upstream on :$MOCK_PORT"
cd "$WORK"
python3 "$HERE/capture_upstream.py" "$MOCK_PORT" "$WORK/upstream.jsonl" \
  "$HERE/../upstream.sse" > "$WORK/mock.log" 2>&1 &
PIDS+=($!)
wait_port "$MOCK_PORT" || { echo "  mock did not start"; cat "$WORK/mock.log"; exit 2; }
ok "capture upstream listening"

# ------------------------------------------------------------- gateway
step "Start agentgateway on :$CONV_PORT and :$CTRL_PORT"
"$AGW" -f "$HERE/agentgateway-config.yaml" > "$WORK/gw.log" 2>&1 &
PIDS+=($!)
wait_port "$CONV_PORT" || { echo "  gateway did not start"; tail -20 "$WORK/gw.log"; exit 2; }
wait_port "$CTRL_PORT" || { echo "  gateway did not bind :$CTRL_PORT"; tail -20 "$WORK/gw.log"; exit 2; }
ok "agentgateway listening on both routes"

# The SDK scripts reach the mock through the gateway, and read the route from
# the environment so this script and the writeup cannot drift apart.
export BASE_CONV="$CONV_PORT" BASE_CTRL="$CTRL_PORT" WITH_TOOLS=1

# ------------------------------------------- rung 0: the upstream is sound
step "Rung 0, capture upstream serves the Anthropic tool-use stream"
stage "$HERE/../upstream.sse"
RUNG0=$(curl -s -o "$WORK/r0.sse" -w '%{http_code} %{content_type}' \
  -X POST "http://127.0.0.1:$MOCK_PORT/v1/messages" \
  -H 'content-type: application/json' -d '{"model":"m","stream":true}')
[[ "$RUNG0" == 200*text/event-stream* ]] \
  && ok "upstream returned 200 text/event-stream" \
  || bad "upstream returned '$RUNG0'"
grep -q '"input_tokens":10' "$WORK/r0.sse" \
  && ok "upstream carries input_tokens in message_start (Anthropic documented shape)" \
  || bad "upstream is missing the documented input_tokens placement"
# The provider must not itself emit a null union tag.
if grep -q '"type": *null' "$WORK/r0.sse"; then
  bad "upstream emitted type:null, the defect would not be the gateway's"
else
  ok "upstream emits no null union tag"
fi

# ------------------------------------ rung 1: passthrough control, no conversion
step "Rung 1, CONTROL, same-dialect passthrough :$CTRL_PORT (no conversion)"
stage "$HERE/../control-passthrough.sse"
if OUT=$(python3 "$HERE/consumer.py" ctrl 2>&1); then
  if grep -q "final tool_calls *\['get_weather'\]" <<<"$OUT"; then
    ok "openai-python accumulated the tool call and returned a completion"
  else
    bad "control did not produce a completed tool call"; echo "$OUT" | sed 's/^/      /'
  fi
  grep -q '"prompt_tokens": 10' <<<"$OUT" \
    && ok "control reports the correct prompt_tokens" \
    || bad "control reported the wrong prompt_tokens"
else
  bad "control raised"; echo "$OUT" | sed 's/^/      /'
fi

# ------------------------------- rung 2: the defect, cross-dialect conversion
step "Rung 2, FAILING PATH, Anthropic -> OpenAI conversion :$CONV_PORT"
stage "$HERE/../upstream.sse"
OUT2=$(python3 "$HERE/consumer.py" conv 2>&1)
if grep -q 'assert new_tool.type == "function"' <<<"$OUT2"; then
  ok "openai-python raised AssertionError at _completions.py:407"
else
  bad "expected AssertionError from the accumulating API"; echo "$OUT2" | sed 's/^/      /'
fi
# The naive iterator must still succeed, or the claim is broader than stated.
if grep -q 'reassembled tool calls' <<<"$OUT2"; then
  ok "the naive chunk iterator still reassembles, so the blast radius is the accumulating API"
else
  bad "the naive iterator also failed, which contradicts the writeup"
fi

step "Rung 2b, determinism, 8 runs of the failing path"
HITS=0
for _ in $(seq 1 8); do
  OUT=$(python3 "$HERE/consumer.py" conv 2>&1)
  grep -q 'assert new_tool.type == "function"' <<<"$OUT" && HITS=$((HITS + 1))
done
[[ "$HITS" -eq 8 ]] && ok "8 of 8 raised" || bad "$HITS of 8 raised, expected 8"

# ------------------------------------- rung 3: buffered control, same conversion
step "Rung 3, CONTROL, same conversion with stream:false"
BUF="$WORK/buffered.json"
cat > "$BUF" <<'JSON'
{"id":"msg_2","type":"message","role":"assistant","model":"captured-model",
 "content":[{"type":"tool_use","id":"toolu_01","name":"get_weather","input":{"city":"sf"}}],
 "stop_reason":"tool_use",
 "usage":{"input_tokens":10,"output_tokens":5,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}
JSON
stage "$BUF"
if OUT=$(ALSO_NONSTREAM=1 python3 "$HERE/consumer.py" conv 2>&1); then
  if grep -q "tool_calls *\['get_weather'\]" <<<"$OUT"; then
    ok "the buffered conversion returns a complete, correct tool call"
  else
    bad "buffered conversion did not return the tool call"; echo "$OUT" | sed 's/^/      /'
  fi
else
  bad "buffered conversion raised unexpectedly"; echo "$OUT" | sed 's/^/      /'
fi

# ------------------------------------------- rung 4: the byte, gateway removed
step "Rung 4, isolate the byte with agentgateway not in the loop"
python3 "$HERE/isolate.py" "$ISO_PORT" agw_verbatim > "$WORK/iso.log" 2>&1 &
PIDS+=($!)
wait_port "$ISO_PORT" || { echo "  isolate server did not start"; exit 2; }
ISO=$(python3 "$HERE/variants.py" "$ISO_PORT" 2>&1)
echo "$ISO" | sed 's/^/      /'
for v in type_omitted type_repeated type_null_only; do
  if grep -qE "^$v +OK" <<<"$ISO"; then
    ok "$v does not raise, so the null tag is the trigger"
  else
    bad "$v did not behave as the isolation predicts"
  fi
done
grep -qE "^agw_verbatim +RAISED" <<<"$ISO" \
  && ok "agentgateway's verbatim bytes do raise" \
  || bad "agentgateway's verbatim bytes did not raise, so the isolation is not faithful"

# ------------------------------------------------------- rung 5: the checker
step "Rung 5, frozen invariant via the harness"
if (cd "$HERE/../../.." && cargo test -p kairo --test conformance -- \
      agentgateway_openai_stream_toolcall_type_null_violates \
      openai_stream_toolcall_type_absent_is_conformant \
      openai_passthrough_stream_toolcall_type_is_conformant \
      checker_ignores_streams_without_tool_call_deltas 2>&1 | grep -q "4 passed"); then
  ok "4 of 4 harness assertions pass against the frozen bytes"
else
  bad "harness assertions did not all pass"
fi

# ------------------------------------------------------------------ verdict
step "Verdict"
if [[ "$FAILURES" -eq 0 ]]; then
  echo "  REPRODUCED. Every rung held; the claim stands."
  exit 0
fi
echo "  NOT REPRODUCED CLEANLY. $FAILURES rung(s) failed; see above."
exit 1
