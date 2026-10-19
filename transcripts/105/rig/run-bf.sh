#!/bin/bash
cd "$(dirname "$0")"; set -a; . /Users/atharva/kairo/.env; set +a
pkill -f "bifrost-v230 -app-dir $PWD"; sleep 1
nohup ~/.cache/bf-hunt/bifrost-v230 -app-dir "$PWD" -port 8191 -log-style pretty > bf.log 2>&1 &
for i in $(seq 1 60); do curl -sf localhost:8191/health >/dev/null && break; sleep 1; done; curl -s localhost:8191/health
