#!/bin/bash
# matrix.sh <tag> <runs> <kill> <cell:trigger>...   trigger = e<N> (events) or s<secs>
cd /work; . ${VENV:-/work/venv}/bin/activate
tag=$1; runs=$2; kill=$3; shift 3
for spec in "$@"; do
  cell=${spec%%:*}; trig=${spec#*:}
  for r in $(seq 1 $runs); do
    ./rig.sh ensure > /dev/null 2>&1
    out=runs/$tag-$cell-$trig-$r
    if [[ $trig == e* ]]; then T="--after ${trig#e}"; else T="--after-secs ${trig#s}"; fi
    p=$(python probe.py $cell --kill $kill $T --out $out 2>&1 | tail -1)
    a=$(python analyze.py $out/client-response.raw $cell 2>&1)
    echo "{\"tag\":\"$tag\",\"cell\":\"$cell\",\"trig\":\"$trig\",\"run\":$r,\"probe\":$p,\"an\":$a}" >> results.jsonl
    echo "$tag $cell $trig $r :: $(echo "$a" | python3 -c 'import json,sys
d=json.load(sys.stdin); print(d.get("http"), "finish", d.get("finish") or d.get("stop") or d.get("status"), "seq", d.get("seq") and {k:d["seq"][k] for k in ("n","dups","gaps","nondigit")}, "tool", d.get("tool_seq"), "errs", d.get("errors")[:1], "issues", d.get("issues"), "usage", d.get("usage"))' 2>&1 | cut -c1-600)"
  done
done
