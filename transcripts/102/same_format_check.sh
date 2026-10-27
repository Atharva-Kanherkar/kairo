#!/bin/sh
# Same claim on a same-format Responses -> Responses passthrough route, to show
# the defect is not confined to the cross-dialect hop.
set -eu
if [ "$#" -ne 1 ]; then
  echo "usage: $0 /path/to/switchyard-server" >&2
  exit 2
fi
case_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
: > "$case_dir/forwarded-same-format.jsonl"
python3 "$case_dir/mock_upstream.py" --capture "$case_dir/forwarded-same-format.jsonl" \
  --port 19331 --dialect responses &
mock_pid=$!
"$1" --config "$case_dir/config-same-format.toml" --host 127.0.0.1 --port 19332 \
  >"$case_dir/server-same-format.log" 2>&1 &
server_pid=$!
cleanup() { kill "$server_pid" "$mock_pid" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
i=0
while [ "$i" -lt 80 ]; do
  if python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:19332/health")' 2>/dev/null; then
    break
  fi
  i=$((i + 1)); sleep 0.1
done
python3 "$case_dir/same_format_repro.py"
