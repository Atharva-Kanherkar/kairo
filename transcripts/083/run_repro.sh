#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: $0 /path/to/switchyard-server" >&2
  exit 2
fi

case_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
capture="$case_dir/forwarded.jsonl"
: > "$capture"
python3 "$case_dir/mock_upstream.py" --capture "$capture" &
mock_pid=$!
"$1" --config "$case_dir/config.toml" --host 127.0.0.1 --port 19312 >"$case_dir/server.log" 2>&1 &
server_pid=$!
cleanup() {
  kill "$server_pid" "$mock_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

i=0
while [ "$i" -lt 50 ]; do
  if python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:19312/health")' 2>/dev/null; then
    break
  fi
  i=$((i + 1))
  sleep 0.1
done
python3 "$case_dir/repro.py"
