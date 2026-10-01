#!/usr/bin/env bash
# install-litellm-src.sh VENV REPO
#
# Make a LiteLLM checkout importable from VENV without building its optional Rust
# extension (maturin). The source tree goes on sys.path through a .pth file, a
# minimal dist-info carries the version for importlib.metadata, and `litellm`
# runs the proxy CLI. LiteLLM falls back to Python when the native bridge is
# missing; LITELLM_RUST=false (set by the environment) makes that explicit.
set -euo pipefail
venv=$1
repo=$2
site=$("$venv/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
version=$(sed -n 's/^version = "\(.*\)"/\1/p' "$repo/pyproject.toml" | head -1)
echo "$repo" > "$site/kairo-litellm-src.pth"
dist="$site/litellm-$version.dist-info"
mkdir -p "$dist"
printf 'Metadata-Version: 2.1\nName: litellm\nVersion: %s\n' "$version" > "$dist/METADATA"
printf 'kairo-src\n' > "$dist/INSTALLER"
printf '[console_scripts]\nlitellm = litellm.proxy.proxy_cli:run_server\n' > "$dist/entry_points.txt"
: > "$dist/RECORD"
cat > "$venv/bin/litellm" <<SH
#!$venv/bin/python
import sys
from litellm.proxy.proxy_cli import run_server
sys.exit(run_server())
SH
chmod +x "$venv/bin/litellm"
for sub in enterprise litellm-proxy-extras; do
  if [ -f "$repo/$sub/pyproject.toml" ]; then
    "$venv/bin/pip" install --no-cache-dir -q --no-deps -e "$repo/$sub"
  fi
done
