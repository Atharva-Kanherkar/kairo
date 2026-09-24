#!/bin/bash
# Run every Codex setup twice: default config and web_search="live".
set -u
HERE=$(cd "$(dirname "$0")" && pwd); P=http://127.0.0.1:9998/openai_passthrough/v1; U=http://127.0.0.1:9998/v1
for ws in default live; do
  "$HERE/run_case.sh" E-direct $ws builtin http://127.0.0.1:9999/v1
  "$HERE/run_case.sh" A-builtin-passthrough $ws builtin $P
  "$HERE/run_case.sh" B-builtin-unified $ws builtin $U
  "$HERE/run_case.sh" C-custom-passthrough-noflag $ws custom $P
  "$HERE/run_case.sh" D-custom-passthrough-flag $ws custom $P true
done
