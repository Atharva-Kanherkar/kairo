#!/bin/bash
# usage: oc.sh <run-name> <model> <variant|-> <baseURL> <prompt>
NAME=$1; MODEL=$2; VARIANT=$3; BASE=$4; PROMPT=$5
OPTS=${MODEL_OPTS:-"{}"}; D=$HOME/.cache/bf-oc/runs/$NAME; rm -rf "$D"; mkdir -p "$D"/{cfg,data,state,cache,work}
cat > "$D/opencode.json" <<JSON
{"\$schema":"https://opencode.ai/config.json","autoupdate":false,"share":"disabled",
 "provider":{"bifrost":{"npm":"@ai-sdk/openai-compatible","name":"Bifrost","options":{"baseURL":"$BASE","apiKey":"${OC_KEY:-sk-none}"},
  "models":{"$MODEL":$OPTS}}}}
JSON
cd "$D/work" && git init -q .
V=(); [ "$VARIANT" != "-" ] && V=(--variant "$VARIANT")
XDG_CONFIG_HOME=$D/cfg XDG_DATA_HOME=$D/data XDG_STATE_HOME=$D/state XDG_CACHE_HOME=$HOME/.cache/bf-oc/occache OPENCODE_CONFIG=$D/opencode.json \
  timeout 400 ${OPENCODE_BIN:-opencode} run --auto --format json -m "bifrost/$MODEL" "${V[@]}" "$PROMPT" < /dev/null > "$D/out.jsonl" 2> "$D/err.txt"
echo "exit=$?"
