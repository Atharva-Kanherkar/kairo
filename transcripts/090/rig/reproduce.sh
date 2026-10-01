#!/usr/bin/env bash
# Cold-start reproduction for the agentgateway tool_result media-loss claim.
#
# Brings up a capture upstream and agentgateway from nothing, then walks the
# differential ladder. Every rung prints PASS or FAIL and the script exits
# non-zero if the claim does not hold, so it is usable as a reviewer gate.
#
#   ./reproduce.sh [path-to-agentgateway-binary]
#
# Requires: python3, curl, and an agentgateway release binary
# (v1.5.0 = fe673247, or v1.6.0-alpha.2 = 02110e2a). No provider credential of
# any kind. The live OpenAI control in the writeup is separate and does need a
# key; this script never touches the network beyond loopback.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGW="${1:-agentgateway}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/kairo090.XXXXXX")"
MOCK_PORT="${MOCK_PORT:-9990}"   # must match hostOverride in agentgateway.yaml
CHAT_PORT=4100      # egress pinned to OpenAI Chat Completions  (suspect)
RESP_PORT=4101      # egress pinned to OpenAI Responses         (control)
ANTH_PORT=4102      # egress pinned to Anthropic Messages       (passthrough control)
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

wait_port() {
  local port="$1" tries=60
  while ((tries-- > 0)); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then exec 3>&- 2>/dev/null; return 0; fi
    sleep 0.25
  done
  return 1
}

# One Anthropic /v1/messages request through a bind. Prints "<http>|<body>".
send() {
  local port="$1" req="$2"
  : > "$WORK/up.jsonl"
  local code
  code=$(curl -s -o "$WORK/client.out" -w '%{http_code}' --max-time 20 \
    -X POST "http://127.0.0.1:$port/v1/messages" \
    -H 'content-type: application/json' -H 'anthropic-version: 2023-06-01' \
    -d @"$HERE/$req")
  local body=""
  [[ -s "$WORK/up.jsonl" ]] && body=$(head -1 "$WORK/up.jsonl" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["body"])')
  printf '%s|%s' "$code" "$body"
}

has_image() { [[ "$1" == *"iVBORw0KGgo"* ]]; }

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

# ------------------------------------------------------- capture upstream
step "Start capture upstream on :$MOCK_PORT"
# Guard before binding, so a stale listener is reported rather than shadowed.
if (exec 3<>"/dev/tcp/127.0.0.1/$MOCK_PORT") 2>/dev/null; then
  exec 3>&- 2>/dev/null
  echo "  capture port $MOCK_PORT is already in use; stop the stale process or pass MOCK_PORT." >&2
  lsof -nP -iTCP:"$MOCK_PORT" -sTCP:LISTEN 2>/dev/null | sed 's/^/    /'
  exit 2
fi
python3 "$HERE/capture.py" "$MOCK_PORT" "$WORK/up.jsonl" > "$WORK/mock.log" 2>&1 &
PIDS+=($!)
wait_port "$MOCK_PORT" || { echo "  capture upstream did not start"; cat "$WORK/mock.log"; exit 2; }
ok "capture upstream recording every forwarded body"

# ------------------------------------------------------------------ gateway
step "Start agentgateway on :$CHAT_PORT :$RESP_PORT :$ANTH_PORT"
# Refuse to run if something already owns a port. A stale gateway left over
# from an earlier run binds the same ports, the new process fails to bind, and
# every request below silently lands on the OLD binary instead. The binds still
# answer, so a readiness probe alone cannot detect this.
for p in "$CHAT_PORT" "$RESP_PORT" "$ANTH_PORT"; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then
    exec 3>&- 2>/dev/null
    echo "  port $p is already in use; stop the stale process first." >&2
    echo "  an older agentgateway or capture upstream answers there, and this" >&2
    echo "  script would test that process instead of $AGW." >&2
    lsof -nP -iTCP:"$p" -sTCP:LISTEN 2>/dev/null | sed 's/^/    /'
    exit 2
  fi
done
# The capture port is a parameter, so rewrite hostOverride into a temp copy
# rather than forcing the caller to edit the checked-in config.
sed "s/hostOverride: 127\.0\.0\.1:9990/hostOverride: 127.0.0.1:$MOCK_PORT/g" \
  "$HERE/agentgateway.yaml" > "$WORK/agentgateway.yaml"
"$AGW" -f "$WORK/agentgateway.yaml" > "$WORK/gw.log" 2>&1 &
GW_PID=$!
PIDS+=("$GW_PID")
for p in "$CHAT_PORT" "$RESP_PORT" "$ANTH_PORT"; do
  wait_port "$p" || { echo "  gateway did not bind :$p"; tail -20 "$WORK/gw.log"; exit 2; }
done
# Confirm the listener we are talking to is the process we just started. If the
# binary under test is not the one bound to the port, every rung below is about
# a different version.
kill -0 "$GW_PID" 2>/dev/null || { echo "  the gateway process exited" >&2; tail -20 "$WORK/gw.log"; exit 2; }
BOUND_PID=$(lsof -nP -iTCP:"$CHAT_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)
if [[ -n "$BOUND_PID" && "$BOUND_PID" != "$GW_PID" ]]; then
  echo "  :$CHAT_PORT is served by pid $BOUND_PID, not the gateway under test ($GW_PID)" >&2
  exit 2
fi
ok "all three egress binds listening, served by the binary under test"

# ------------------------------------------------ rung 0: the claim itself
step "Rung 0, FAILING PATH, Anthropic ingress -> OpenAI Chat Completions"
R0="$(send "$CHAT_PORT" req-toolresult-image.json)"
CODE="${R0%%|*}"; BODY="${R0#*|}"
[[ "$CODE" == "200" ]] \
  && ok "client got HTTP 200, so the loss is silent" \
  || bad "client got HTTP $CODE, the loss is not silent on this version"
has_image "$BODY" \
  && bad "the image survived, the defect does not reproduce" \
  || ok "the image is absent from the forwarded OpenAI body"
grep -q '"role":"tool"' <<<"$BODY" \
  && ok "the tool message itself is present, so only its media part was lost" \
  || bad "the tool message is missing entirely, which is a different defect"
grep -q 'captured the viewport' <<<"$BODY" \
  && ok "the text sibling of the same tool_result survived" \
  || bad "the text sibling was lost too, which contradicts the writeup"
grep -qi 'warn\|unsupported\|cannot be represented' "$WORK/gw.log" \
  && bad "the gateway logged a warning, so the loss is not unannounced" \
  || ok "the gateway logged nothing, the drop is unannounced"

# ------------------------------------------- rung 1: isolation, one variable
step "Rung 1, isolate the trigger"
I1="$(send "$CHAT_PORT" req-user-image.json)"
has_image "${I1#*|}" \
  && ok "CONTROL: the same image in a user turn survives the identical route" \
  || bad "CONTROL: a user-turn image was also lost, so the trigger is not the tool_result position"

I2="$(send "$CHAT_PORT" req-toolresult-text-only.json)"
grep -q 'captured the viewport' <<<"${I2#*|}" \
  && ok "CONTROL: a text-only tool_result is conformant on the same route" \
  || bad "CONTROL: a text-only tool_result was lost, so the claim is broader than stated"

I3="$(send "$CHAT_PORT" req-toolresult-document.json)"
has_image "${I3#*|}" \
  && bad "the document part survived, the claim is broader than stated" \
  || ok "a document part in the same tool_result is lost the same way"

I4="$(send "$CHAT_PORT" req-toolresult-image-stream.json)"
has_image "${I4#*|}" \
  && bad "the streaming request kept the image, which contradicts the writeup" \
  || ok "streaming loses it identically, so the defect is not stream-specific"

# --------------------------- rung 2: same gateway, same request, other egress
step "Rung 2, CONTROL, same request on the Responses egress"
R2="$(send "$RESP_PORT" req-toolresult-image.json)"
CODE2="${R2%%|*}"; BODY2="${R2#*|}"
if [[ "$CODE2" == "200" || "$CODE2" == "4"*[0-9] || "$CODE2" == "502" ]]; then
  if has_image "$BODY2"; then
    ok "the Responses egress carries the image the Chat egress dropped"
  elif [[ "$CODE2" != "200" ]]; then
    ok "the Responses egress fails loudly (HTTP $CODE2) instead of dropping silently"
  else
    bad "the Responses egress dropped it silently too, which contradicts the writeup"
  fi
else
  bad "unexpected Responses egress status $CODE2"
fi

step "Rung 2b, CONTROL, same request on the Anthropic passthrough egress"
R3="$(send "$ANTH_PORT" req-toolresult-image.json)"
has_image "${R3#*|}" \
  && ok "the Anthropic egress carries the image, so the ingress parser is sound" \
  || bad "the Anthropic egress also lost it, so the claim is not about conversion"

# ---------------------------------------------------------- rung 3: N of N
step "Rung 3, determinism, 10 runs of the failing path"
HITS=0
for _ in $(seq 1 10); do
  R="$(send "$CHAT_PORT" req-toolresult-image.json)"
  has_image "${R#*|}" || HITS=$((HITS + 1))
done
[[ "$HITS" -eq 10 ]] \
  && ok "image absent from the forwarded body in 10 of 10 runs" \
  || bad "image absent in $HITS of 10 runs, expected 10"

# ------------------------------------------------------------------ verdict
step "Verdict"
if [[ "$FAILURES" -eq 0 ]]; then
  echo "  REPRODUCED. Every rung held; the claim stands."
  exit 0
fi
echo "  NOT REPRODUCED CLEANLY. $FAILURES rung(s) failed; see above."
exit 1