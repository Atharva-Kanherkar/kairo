#!/bin/bash
# Start Bifrost on :8191 with this rig's config. Needs BIFROST_BIN (transports v2.3.0,
# vcs.revision 9237ec06, core v1.12.0), APP_DIR (scratch directory) and ENV_FILE (dotenv
# with the provider keys the config references by env name).
: "${BIFROST_BIN:?}" "${APP_DIR:?}" "${ENV_FILE:?}"
RIG=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$APP_DIR"; cp "$RIG/${BIFROST_CONFIG:-bifrost-config.json}" "$APP_DIR/config.json"
set -a; . "$ENV_FILE"; set +a
pkill -f "$BIFROST_BIN -app-dir $APP_DIR"; sleep 1
nohup "$BIFROST_BIN" -app-dir "$APP_DIR" -port 8191 -log-style pretty > "$APP_DIR/bf.log" 2>&1 &
for i in $(seq 1 60); do curl -sf localhost:8191/health >/dev/null && break; sleep 1; done; curl -s localhost:8191/health; echo
