#!/bin/bash
# Start the four recording relays under capture root $1.
cd "$(dirname "$0")"; ROOT=$1; mkdir -p "$ROOT"
pkill -f record_proxy.py; sleep 1
start() { LISTEN=$1 TARGET=$2 TARGET_TLS=$3 CAPDIR=$ROOT/$4 nohup python3 record_proxy.py > "$ROOT/relay-$4.log" 2>&1 & }
start 19971 127.0.0.1:8191 "" client-to-bifrost
start 19972 api.anthropic.com:443 1 bifrost-to-anthropic
start 19973 openrouter.ai:443 1 bifrost-to-openrouter
start 19974 openrouter.ai:443 1 client-to-openrouter-direct
sleep 1; for p in 19971 19972 19973 19974; do lsof -iTCP:$p -sTCP:LISTEN >/dev/null && echo "relay $p up" || echo "relay $p DOWN"; done
