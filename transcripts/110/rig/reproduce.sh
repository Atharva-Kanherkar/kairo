#!/bin/bash
# Cold-start reproduction for 110. Needs uv, git, python3 and OPENAI_API_KEY in the
# environment (or KAIRO_ENV pointing at a dotenv file that sets it).
# Builds three LiteLLM proxies from one shallow fetch: main cfe0100e (contains #48534),
# its pre-#48534 parent 74ce6d1d, and main + fix.diff (the rejected candidate fix).
# Each runs behind a recording relay to the live OpenAI API. Sends one /v1/messages
# request per model, stream mode and build, then one LangChain ReAct run per build.
set -euo pipefail
RIG=$(cd "$(dirname "$0")" && pwd)
WORK=${WORK:-$(mktemp -d)}
[ -n "${KAIRO_ENV:-}" ] && { set -a; . "$KAIRO_ENV"; set +a; }
: "${OPENAI_API_KEY:?set OPENAI_API_KEY}"
trap 'pkill -f "$WORK/run/" || true' EXIT
MAIN=cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a
PRE=74ce6d1d66d22626fad72fbb9219d3cadf134b77
echo "work dir: $WORK"
git init -q "$WORK/litellm"
git -C "$WORK/litellm" remote add origin https://github.com/BerriAI/litellm.git
git -C "$WORK/litellm" fetch -q --depth 1 origin "$MAIN" "$PRE"
for b in "main $MAIN" "pre $PRE" "fix $MAIN"; do set -- $b; git -C "$WORK/litellm" worktree add -q --detach "$WORK/$1" "$2"; done
git -C "$WORK/fix" apply "$RIG/fix.diff"
uv venv -q -p 3.12 "$WORK/venv"; VIRTUAL_ENV=$WORK/venv uv pip install -q -e "$WORK/main[proxy]"
uv venv -q -p 3.12 "$WORK/lc"; VIRTUAL_ENV=$WORK/lc uv pip install -q langchain-anthropic==1.7.6 langchain-classic==1.0.8
start() { # build proxy_port relay_port
  mkdir -p "$WORK/run/$1"; sed "s/4098/$3/g" "$RIG/config-recorded.yaml" > "$WORK/run/$1/config.yaml"
  python3 "$RIG/relay.py" "$3" "$WORK/run/$1/fwd" > "$WORK/run/$1/relay.log" 2>&1 &
  (cd "$WORK/run/$1" && PYTHONPATH="$WORK/$1" "$WORK/venv/bin/python" -m litellm.proxy.proxy_cli --config "$WORK/run/$1/config.yaml" --port "$2" --host 127.0.0.1 > proxy.log 2>&1 &)
  for _ in $(seq 1 180); do curl -sf "localhost:$2/health/liveliness" >/dev/null && break; sleep 1; done
  echo "$1 imports $(PYTHONPATH="$WORK/$1" "$WORK/venv/bin/python" -c 'import litellm; print(litellm.__file__)' 2>/dev/null)"
}
start main 4031 4131; start pre 4032 4132; start fix 4033 4133
for m in g54m g55 g41m; do for mode in plain stream; do for b in "main 4031" "pre 4032" "fix 4033"; do set -- $b
  echo "$m $mode stop $1: $("$WORK/venv/bin/python" "$RIG/wire.py" "$2" "$m" "$mode" stop "$WORK/out/$m-$mode-$1" "$WORK/run/$1/fwd")"
done; done; done
for m in g54m g55; do
  echo "$m plain nostop main: $("$WORK/venv/bin/python" "$RIG/wire.py" 4031 "$m" plain nostop "$WORK/out/$m-nostop-main" "$WORK/run/main/fwd")"
done
for b in "main 4031" "pre 4032" "fix 4033"; do set -- $b
  echo "react g54m $1: $("$WORK/lc/bin/python" "$RIG/react_agent.py" "http://127.0.0.1:$2" g54m "$WORK/agent-$1" 2>/dev/null | cut -c1-200)"
done
echo "react g41m main: $("$WORK/lc/bin/python" "$RIG/react_agent.py" http://127.0.0.1:4031 g41m "$WORK/agent-g41m" 2>/dev/null | cut -c1-200)"
