#!/bin/bash
cd "$(dirname "$0")"
BASE=$1; shift
P=${PROMPT:-'Create notes.txt containing the line alpha. Then read it back, then append a line beta, then run the shell command "wc -l notes.txt" and tell me the number.'}
for spec in "$@"; do
  read -r N M V <<< "$spec"
  ./oc.sh "$N" "$M" "$V" "$BASE" "$P" >/dev/null
  echo "$N $M $V :: file=[$(tr '\n' ' ' < runs/$N/work/notes.txt 2>/dev/null)] steps=$(grep -c step_finish runs/$N/out.jsonl) errors=$(grep -c '"type":"error"' runs/$N/out.jsonl)"
  grep -h -o '"message":"[^"]*' runs/$N/out.jsonl runs/$N/err.txt | head -2
done
