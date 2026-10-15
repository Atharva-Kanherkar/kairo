#!/bin/sh
# Finding 101 reproduction: Responses host tools on a cross-format route.
#
# usage: run_repro.sh /path/to/switchyard-server [output-directory]
#
# The capture records include the forwarded Authorization header. The rig sets
# it to the canary string below; never point KAIRO_CANARY_KEY at a real key.
#
# The output directory defaults to a fresh scratch directory. Pass a directory
# to freeze a new set of capture files; the committed capture-*.jsonl files are
# the frozen record and are only rewritten when a directory is given on purpose.
set -eu

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: $0 /path/to/switchyard-server [output-directory]" >&2
  exit 2
fi

case_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
out_dir=${2:-$(mktemp -d "${TMPDIR:-/tmp}/kairo-101.XXXXXX")}
mkdir -p "$out_dir"

KAIRO_CANARY_KEY="canary-not-a-credential" \
  python3 "$case_dir/capture.py" 9996 "$out_dir/.upstream.jsonl" &
capture_pid=$!

KAIRO_CANARY_KEY="canary-not-a-credential" \
  "$1" --config "$case_dir/config.toml" --host 127.0.0.1 --port 19412 \
  >"$out_dir/server.log" 2>&1 &
server_pid=$!

cleanup() {
  kill "$server_pid" "$capture_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

i=0
while [ "$i" -lt 60 ]; do
  if python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:19412/health")' 2>/dev/null; then
    break
  fi
  i=$((i + 1))
  sleep 0.2
done

rm -f "$out_dir"/capture-*.jsonl
python3 "$case_dir/repro.py" "$out_dir" "http://127.0.0.1:19412/v1/responses"