#!/bin/bash
# Cold-start reproduction for 107. Needs uv, git, python3, and GEMINI_API_KEY in
# the environment (or KAIRO_ENV pointing at a dotenv file that sets it).
# Builds three proxies: LiteLLM v1.106.2, v1.88.2 (PyPI), and v1.106.2 + fix.diff,
# each behind a recording relay to the live Gemini API, then sends one request
# per dialect and model to each and prints status lines.
set -euo pipefail
RIG=$(cd "$(dirname "$0")" && pwd)
WORK=${WORK:-$(mktemp -d)}
[ -n "${KAIRO_ENV:-}" ] && { set -a; . "$KAIRO_ENV"; set +a; }
: "${GEMINI_API_KEY:?set GEMINI_API_KEY}"
echo "work dir: $WORK"
git clone -q --depth 1 --branch v1.106.2 https://github.com/BerriAI/litellm.git "$WORK/litellm"
cp -R "$WORK/litellm" "$WORK/litellm-fix"; git -C "$WORK/litellm-fix" apply "$RIG/fix.diff"
for b in cur ctl fix; do uv venv -q -p 3.12 "$WORK/venv-$b"; done
VIRTUAL_ENV=$WORK/venv-cur uv pip install -q -e "$WORK/litellm[proxy]"
VIRTUAL_ENV=$WORK/venv-fix uv pip install -q -e "$WORK/litellm-fix[proxy]"
VIRTUAL_ENV=$WORK/venv-ctl uv pip install -q "litellm[proxy]==1.88.2"
start() { # build proxy_port relay_port
  mkdir -p "$WORK/$1"; sed "s/4098/$3/g" "$RIG/config-recorded.yaml" > "$WORK/$1/config.yaml"
  python3 "$RIG/relay.py" "$3" "$WORK/$1/fwd" > "$WORK/$1/relay.log" 2>&1 &
  (cd "$WORK/$1" && "$WORK/venv-$1/bin/litellm" --config config.yaml --port "$2" > proxy.log 2>&1 &)
  for _ in $(seq 1 120); do curl -sf "localhost:$2/health/liveliness" >/dev/null && return; sleep 1; done
  echo "proxy $1 did not start"; exit 1
}
start cur 4014 4098; start ctl 4015 4099; start fix 4017 4097
for d in chat messages responses; do for m in g25p g25fl g38f; do
  for b in "cur 4014 v1.106.2" "ctl 4015 v1.88.2" "fix 4017 patched"; do
    set -- $b
    echo "$d $m $3: $("$WORK/venv-cur/bin/python" "$RIG/wire.py" "$2" "$d" "$m" "$WORK/out/$d-$m-$3")"
  done
done; done
echo "forwarded bytes: $WORK/{cur,ctl,fix}/fwd"
