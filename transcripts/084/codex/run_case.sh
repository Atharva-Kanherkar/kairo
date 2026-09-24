#!/bin/bash
# usage: run_case.sh <name> <ws: default|live> <kind: builtin|custom> <base_url> [standalone_flag]
set -u
RIG=${RIG:-/tmp/k084c}
# sk-review-master is the synthetic LITELLM_MASTER_KEY set by start_rig.sh, not a real credential.
NAME=$1; WS=$2; KIND=$3; BASE=$4; FLAG=${5:-}
R=$RIG/runs/$NAME-$WS
rm -rf "$R"; mkdir -p "$R/work" "$R/home"
echo "$R" > $RIG/runs/CURRENT
CODEX=${CODEX:-codex}
ARGS=(exec --skip-git-repo-check -s read-only -m gpt-5.6-luna -C "$R/work")
[ "$WS" = live ] && ARGS+=(-c 'web_search="live"')
if [ "$KIND" = builtin ]; then
  KEY=sk-review-master; [[ "$BASE" == *9999* ]] && KEY=dummy-direct-tap-injects
  printf '%s' "$KEY" | CODEX_HOME="$R/home" $CODEX login --with-api-key > "$R/login.log" 2>&1
  ARGS+=(-c "openai_base_url=\"$BASE\"")
else
  ARGS+=(-c 'model_provider="litellm"' -c 'model_providers.litellm.name="litellm"' -c "model_providers.litellm.base_url=\"$BASE\"" -c 'model_providers.litellm.env_key="LITELLM_KEY"' -c 'model_providers.litellm.wire_api="responses"')
  [ -n "$FLAG" ] && ARGS+=(-c "model_providers.litellm.supports_standalone_web_search=$FLAG")
fi
printf '%q ' "${ARGS[@]}" > "$R/codex-args.txt"
PROMPT='Use web search to find the most recent release tag of the GitHub project BerriAI/litellm. You must search the web; do not guess or rely on memory. Reply with the tag and the URL you found it at.'
( CODEX_HOME="$R/home" LITELLM_KEY=sk-review-master $CODEX "${ARGS[@]}" "$PROMPT" > "$R/stdout.txt" 2> "$R/stderr.txt"; echo $? > "$R/exit.txt" ) &
PID=$!; for i in $(seq 1 240); do kill -0 $PID 2>/dev/null || break; sleep 1; done
kill -0 $PID 2>/dev/null && { pkill -P $PID; kill $PID; echo timeout > "$R/exit.txt"; }
rm -f "$R/home/auth.json"
echo "== $NAME-$WS exit=$(cat $R/exit.txt)"
for f in "$R"/client/*-request.json "$R"/upstream/*-request.json "$R"/direct/*-request.json; do [ -f "$f" ] || continue; n=${f%-request.json}; printf '%s %s %s -> %s\n' "$(basename $(dirname $f))" "$(python3 -c "import json;d=json.load(open('$f'));print(d['method'],d['path'])")" "" "$(python3 -c "import json;print(json.load(open('$n-response.json'))['status'])")"; done | sort | uniq -c
