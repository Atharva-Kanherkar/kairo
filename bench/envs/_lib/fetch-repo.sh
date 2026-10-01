#!/usr/bin/env bash
# fetch-repo.sh URL COMMIT DEST [DEPTH]
#
# Check out a real upstream repository at an exact commit with DEPTH commits of
# history before it, and nothing after it: no remote, no branches or tags that
# point past the base, so the fix cannot be read from local history. The base
# is tagged `kairo-base`; the harness diffs against it.
set -euo pipefail
url=$1
commit=$2
dest=$3
depth=${4:-200}

git init -q "$dest"
cd "$dest"
# Keep Git LFS pointer files as-is; no environment needs LFS payloads.
git config filter.lfs.smudge cat
git config filter.lfs.process ""
git config filter.lfs.required false
git remote add origin "$url"
git fetch -q --no-tags --depth "$depth" origin "$commit"
git checkout -q --detach FETCH_HEAD
actual=$(git rev-parse HEAD)
if [ "$actual" != "$commit" ]; then
  echo "fetched $actual, expected $commit" >&2
  exit 1
fi
git remote remove origin
git checkout -q -B main
git tag kairo-base
git config user.name "kairo agent"
git config user.email "agent@kairo.invalid"
git config advice.detachedHead false
git reflog expire --expire=now --all
git gc -q --prune=now
