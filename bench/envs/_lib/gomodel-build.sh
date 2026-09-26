#!/usr/bin/env bash
# gomodel-build [OUT]: build the GoModel gateway from /work/repo (warm Go cache).
set -euo pipefail
out=${1:-/work/bin/gomodel}
mkdir -p "$(dirname "$out")"
cd /work/repo
go build -o "$out" ./cmd/gomodel
