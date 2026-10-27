#!/bin/bash
# usage: start.sh <tool-parser> <reasoning-parser|none> [model]
# PY selects the interpreter (default python), for example a venv holding ai-dynamo 1.5.0.
pkill -f scripted_engine; pkill -f dynamo.frontend; sleep 1
export DYN_FILE_KV=/tmp/dynkv; rm -rf /tmp/dynkv
MODEL=${3:-Qwen/Qwen3-0.6B}
PY=${PY:-python}
RP=""; [ "$2" != "none" ] && RP="--reasoning-parser $2"
TP=""; [ "$1" != "none" ] && TP="--tool-call-parser $1"
cd /rig
nohup $PY -m dynamo.frontend --http-port 8000 --discovery-backend file --enable-anthropic-api > /rig/logs/frontend.log 2>&1 &
TOKEN_DELAY=${TOKEN_DELAY:-0.002} nohup $PY /rig/scripted_engine.py --model $MODEL $TP $RP --discovery-backend file > /rig/logs/engine.log 2>&1 &
for i in $(seq 1 120); do curl -sf localhost:8000/v1/models | grep -q '"id"' && break; sleep 1; done
curl -s localhost:8000/v1/models
