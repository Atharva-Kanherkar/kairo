#!/bin/bash
cd "$(dirname "$0")"; set -a; . /Users/atharva/kairo/.env; set +a
export OPENCODE_BIN=$HOME/.cache/oc1193/node_modules/.bin/opencode
for i in 1 2 3 4 5; do
  ./batch.sh http://localhost:19971/openai "oc-bifrost-$i openrouter/anthropic/claude-haiku-4.5 -"
  OC_KEY=$OPENROUTER_API_KEY ./batch.sh http://localhost:19974/api/v1 "oc-direct-$i anthropic/claude-haiku-4.5 -"
done
