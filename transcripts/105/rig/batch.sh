#!/bin/bash
# usage: batch.sh <baseURL> "<run-name> <model> <variant|->" ...
cd "$(dirname "$0")"
RUNS_DIR=${RUNS_DIR:-/tmp/kairo105-runs}; export RUNS_DIR
BASE=$1; shift
P=${PROMPT:-'Create notes.txt containing the line alpha. Then read it back, then append a line beta, then run the shell command "wc -l notes.txt" and tell me the number.'}
for spec in "$@"; do
  read -r N M V <<< "$spec"
  ./oc.sh "$N" "$M" "$V" "$BASE" "$P" >/dev/null
  R=$RUNS_DIR/$N
  echo "$N $M $V :: file=[$(tr '\n' ' ' < $R/work/notes.txt 2>/dev/null)] steps=$(grep -c step_finish $R/out.jsonl) errors=$(grep -c '"type":"error"' $R/out.jsonl)"
  grep -h -o '"message":"[^"]*' $R/out.jsonl $R/err.txt | head -2
done
