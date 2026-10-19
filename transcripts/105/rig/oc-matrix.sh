#!/bin/bash
# Real opencode sessions through Bifrost (client relay 19971) and direct to OpenRouter
# (relay 19974). Needs ENV_FILE (a dotenv defining OPENROUTER_API_KEY) and OPENCODE_BIN
# (opencode 1.19.3). RUNS sessions per arm, default 5.
cd "$(dirname "$0")"
: "${ENV_FILE:?set ENV_FILE to a dotenv that defines OPENROUTER_API_KEY}"
: "${OPENCODE_BIN:?set OPENCODE_BIN to an opencode 1.19.3 binary}"
set -a; . "$ENV_FILE"; set +a; export OPENCODE_BIN
for i in $(seq 1 "${RUNS:-5}"); do
  ./batch.sh http://localhost:19971/openai "oc-bifrost-$i openrouter/anthropic/claude-haiku-4.5 -"
  OC_KEY=$OPENROUTER_API_KEY ./batch.sh http://localhost:19974/api/v1 "oc-direct-$i anthropic/claude-haiku-4.5 -"
done
