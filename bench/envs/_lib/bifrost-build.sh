#!/usr/bin/env bash
# bifrost-build [OUT]: build the Bifrost HTTP gateway from /work/repo (Go workspace).
# Incremental: the environment image ships a warm Go build cache.
set -euo pipefail
out=${1:-/work/bin/bifrost-http}
mkdir -p "$(dirname "$out")"
cd /work/repo/transports/bifrost-http
go build -o "$out" .
