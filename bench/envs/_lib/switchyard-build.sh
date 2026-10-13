#!/usr/bin/env bash
# switchyard-build: build switchyard-server from /work/repo (dev profile, no debug info).
# Prints the binary path. Incremental: the environment image ships a warm target dir.
set -euo pipefail
cd /work/repo
cargo build --locked -q -p switchyard-server
echo /work/repo/target/debug/switchyard-server
