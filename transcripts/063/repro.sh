#!/bin/bash
# Replay the 063 redirect leak with live keys from the repo .env.
# Writes redacted transcripts only. Rotate keys after.
# Usage: SWITCHYARD_BIN=/path/to/switchyard-server ./transcripts/063/repro.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export SWITCHYARD_BIN="${SWITCHYARD_BIN:-$ROOT/tools/switchyard/target/release/switchyard-server}"
python3 "$ROOT/transcripts/063/live_real.py"
