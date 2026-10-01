#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PYTHONPATH=/opt/kairo-lib${PYTHONPATH:+:$PYTHONPATH} python3 reproduce.py "$@"
