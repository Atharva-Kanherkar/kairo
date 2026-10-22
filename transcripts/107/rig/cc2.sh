#!/bin/bash
RIG=$(cd "$(dirname "$0")" && pwd)
# usage: cc2.sh <base_url> <model> <tag>   -> one-line result
B=$1; M=$2; T=$3; D=$PWD/cc2-$M-$T; rm -rf $D; mkdir -p $D/work $D/cfg
cp "$RIG/code.png" $D/work/; cd $D/work
export CLAUDE_CONFIG_DIR=$D/cfg ANTHROPIC_BASE_URL=$B ANTHROPIC_AUTH_TOKEN=sk-test-master ANTHROPIC_API_KEY= \
 ANTHROPIC_SMALL_FAST_MODEL=$M ANTHROPIC_DEFAULT_HAIKU_MODEL=$M DISABLE_TELEMETRY=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
timeout 300 claude -p "Read the image file code.png with the Read tool, then write the exact text it shows into answer.txt." \
  --model $M --dangerously-skip-permissions --output-format json < /dev/null > $D/out.json 2> $D/err.txt; E1=$?
SID=$(python3 -c "import json;print(json.load(open('$D/out.json')).get('session_id',''))" 2>/dev/null)
# follow-up turn in the same session, text only
timeout 300 claude -p --resume "$SID" "Now write the word OK into ok.txt." \
  --model $M --dangerously-skip-permissions --output-format json < /dev/null > $D/out2.json 2> $D/err2.txt; E2=$?
python3 - "$D" "$E1" "$E2" <<'PY'
import json,sys,glob
d,e1,e2=sys.argv[1:]
def res(p):
    try: j=json.load(open(p)); return j.get("is_error"), str(j.get("result"))[:70].replace("\n"," ")
    except Exception as ex: return None, "noparse"
img=0
for f in glob.glob(d+"/cfg/projects/*/*.jsonl"):
    for l in open(f):
        e=json.loads(l)
        if e.get("type")=="user" and isinstance(e["message"]["content"],list):
            for b in e["message"]["content"]:
                if b.get("type")=="tool_result" and isinstance(b.get("content"),list) and any(x.get("type")=="image" for x in b["content"]): img+=1
def rd(p):
    try: return open(p).read().strip()[:20]
    except: return "-"
i1,r1=res(d+"/out.json"); i2,r2=res(d+"/out2.json")
print(f"exit1={e1} err1={i1} img_results={img} answer={rd(d+'/work/answer.txt')!r} | exit2={e2} err2={i2} ok={rd(d+'/work/ok.txt')!r} | r1={r1!r} | r2={r2!r}")
PY
