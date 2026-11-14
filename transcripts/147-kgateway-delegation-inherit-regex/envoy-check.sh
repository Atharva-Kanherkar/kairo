#!/bin/sh
# Data-plane check for kairo issue 147.
# The child route is first because the listener sorts a RegularExpression match
# above a PathPrefix match. Envoy evaluates routes in order, first match wins.
set -eu
for path in /admin /admin/ /admin/secret /team1/x; do
  body=$(curl -sS -o - -w ' status=%{http_code}' -H 'Host: example.com' "http://127.0.0.1:10000${path}")
  printf '%-14s -> %s\n' "$path" "$body"
done
