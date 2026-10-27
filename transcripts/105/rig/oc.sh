#!/bin/bash
# usage: oc.sh <run-name> <model> <variant|-> <baseURL> <prompt>
# The provider key is never written to disk: opencode.json references {env:OC_KEY}
# and the value stays in the process environment.
NAME=$1; MODEL=$2; VARIANT=$3; BASE=$4; PROMPT=$5
RUNS_DIR=${RUNS_DIR:-/tmp/kairo105-runs}
OPTS=${MODEL_OPTS:-"{}"}; D=$RUNS_DIR/$NAME; rm -rf "$D"; mkdir -p "$D"/{cfg,data,state,work}
cat > "$D/opencode.json" <<JSON
{"\$schema":"https://opencode.ai/config.json","autoupdate":false,"share":"disabled",
 "provider":{"bifrost":{"npm":"@ai-sdk/openai-compatible","name":"Bifrost","options":{"baseURL":"$BASE","apiKey":"{env:OC_KEY}"},
  "models":{"$MODEL":$OPTS}}}}
JSON
export OC_KEY=${OC_KEY:-sk-none}
cd "$D/work" && git init -q .
V=(); [ "$VARIANT" != "-" ] && V=(--variant "$VARIANT")
XDG_CONFIG_HOME=$D/cfg XDG_DATA_HOME=$D/data XDG_STATE_HOME=$D/state XDG_CACHE_HOME=${OC_CACHE:-$RUNS_DIR/.occache} OPENCODE_CONFIG=$D/opencode.json \
  timeout 400 ${OPENCODE_BIN:-opencode} run --auto --format json -m "bifrost/$MODEL" "${V[@]}" "$PROMPT" < /dev/null > "$D/out.jsonl" 2> "$D/err.txt"
echo "exit=$?"
