#!/usr/bin/env bash
# One-hour rectangular sweep, then a draft PR with the matrix.
#
#   scripts/sweep-and-pr.sh                  # 60 minutes, capture only, draft PR
#   scripts/sweep-and-pr.sh --live           # add the live impact leg (real keys)
#   scripts/sweep-and-pr.sh --minutes 20     # shorter budget
#   scripts/sweep-and-pr.sh --no-pr          # run and write files, do not push
#
# Everything after the flags below is passed through to the sweep module.
set -euo pipefail

cd "$(dirname "$0")/.."

MINUTES=60
LIVE=""
PR="--open-pr"
EXTRA=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --minutes) MINUTES="$2"; shift 2 ;;
    --live)    LIVE="--live"; shift ;;
    --no-pr)   PR=""; shift ;;
    *)         EXTRA+=("$1"); shift ;;
  esac
done

echo "== preflight =="

if [[ -n "$(git status --porcelain)" ]]; then
  echo "working tree is dirty. Commit or stash first: the sweep commits to a"
  echo "branch of its own and will not mix your changes into it."
  git status --short
  exit 1
fi

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ -n "$PR" && "$branch" != "main" ]]; then
  echo "on branch '$branch'. Run from main, or pass --no-pr."
  exit 1
fi

echo "-- corpus self-check"
python3 -m tools.sweep.sweep --dry-run

echo "-- suite green before we start"
cargo test -p kairo >/dev/null
python3 tools/update-readme-counts.py --check

echo
echo "== sweep: ${MINUTES}m budget =="
echo "Gateways are attached if already listening, launched if their binary is"
echo "on PATH, and recorded as not-run otherwise. An absent gateway shows up in"
echo "the matrix as blank cells with a reason, never as a clean column."
echo

# shellcheck disable=SC2086
python3 -m tools.sweep.sweep \
  --minutes "$MINUTES" \
  $LIVE \
  $PR \
  "${EXTRA[@]+"${EXTRA[@]}"}"

echo
echo "== done =="
echo "issues/MATRIX.md      the coverage matrix"
echo "issues/CANDIDATES.md  ranked leads, nothing auto-filed"
echo "transcripts/sweep/    frozen bytes per cell"
