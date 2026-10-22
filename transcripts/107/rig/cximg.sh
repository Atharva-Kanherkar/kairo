#!/bin/bash
RIG=$(cd "$(dirname "$0")" && pwd)
# usage: cx.sh <model> <tag>
B=${BASE:-http://localhost:4012}; M=$1; T=$2; D=$PWD/cximg-$M-$T; rm -rf $D; mkdir -p $D/work $D/home
cat > $D/home/config.toml <<T
model = "$M"
model_provider = "litellm"
[model_providers.litellm]
name = "litellm"
base_url = "$B/v1"
wire_api = "responses"
env_key = "LITELLM_API_KEY"
[projects."$D/work"]
trust_level = "trusted"
T
cd $D/work
cp "$RIG/code.png" .
CODEX_HOME=$D/home LITELLM_API_KEY=sk-test-master timeout 300 codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox \
 "Use your view_image tool to look at code.png, then write the exact text it shows into answer.txt." < /dev/null > $D/out.txt 2>&1
echo "exit=$? answer=$(cat answer.txt 2>/dev/null)"; tail -5 $D/out.txt
