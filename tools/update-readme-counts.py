#!/usr/bin/env python3
"""Rewrite the README Status counts from the repo.

Bugs = unique git-tracked issues/NNN-* writeups.
Tests = #[test] in the harness (conformance + unit).

  python3 tools/update-readme-counts.py          # write README.md
  python3 tools/update-readme-counts.py --check  # fail if README is stale
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
START = "<!-- kairo-counts:start -->"
END = "<!-- kairo-counts:end -->"

GATEWAYS = (
    ("litellm", "LiteLLM"),
    ("switchyard", "NVIDIA Switchyard"),
    ("bifrost", "Bifrost"),
    ("gomodel", "GoModel"),
)

TEST_RE = re.compile(r"^\s*#\[test\]", re.M)
ISSUE_RE = re.compile(r"^issues/(\d+)-([^/]+)/README\.md$")


def git_ls(pattern: str) -> list[str]:
    out = subprocess.check_output(
        ["git", "ls-files", "-z", pattern],
        cwd=ROOT,
        text=True,
    )
    return [p for p in out.split("\0") if p]


def count_tests(rel: str) -> int:
    return len(TEST_RE.findall((ROOT / rel).read_text()))


def collect() -> dict:
    issues: dict[int, str] = {}
    for path in git_ls("issues/*/README.md"):
        m = ISSUE_RE.match(path)
        if not m:
            continue
        issues[int(m.group(1))] = m.group(2)
    slugs = " ".join(issues.values())
    gateways = [label for token, label in GATEWAYS if token in slugs]
    conformance = count_tests("crates/harness/tests/conformance.rs")
    unit = count_tests("crates/harness/src/checks.rs") + count_tests(
        "crates/harness/src/lib.rs"
    )
    return {
        "bugs": len(issues),
        "conformance": conformance,
        "unit": unit,
        "tests": conformance + unit,
        "gateways": gateways,
    }


def join_and(items: list[str]) -> str:
    if not items:
        return "the gateways under test"
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def render(counts: dict) -> str:
    gateways = join_and(counts["gateways"])
    return (
        f"{START}\n"
        f"{counts['bugs']} distinct defects reproduced and documented across "
        f"{gateways} (see [`issues/SCOREBOARD.md`](issues/SCOREBOARD.md)). "
        f"One is already filed upstream as "
        f"[NVIDIA-NeMo/Switchyard#380](https://github.com/NVIDIA-NeMo/Switchyard/issues/380).\n"
        f"The Rust harness is green with {counts['tests']} tests, of which "
        f"{counts['conformance']} are conformance checks wired to recorded "
        f"transcripts. This is early and active; contributions of new "
        f"reproductions are the fastest way to help.\n"
        f"{END}"
    )


def apply(text: str, block: str) -> str:
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pattern.search(text):
        raise SystemExit(
            f"README.md is missing {START} / {END} markers around the Status counts"
        )
    return pattern.sub(block, text, count=1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if README Status counts do not match the repo",
    )
    args = parser.parse_args()
    counts = collect()
    block = render(counts)
    current = README.read_text()
    updated = apply(current, block)
    if args.check:
        if updated != current:
            print(
                "README Status counts are stale. Run "
                "`python3 tools/update-readme-counts.py` and commit README.md.",
                file=sys.stderr,
            )
            print(
                f"counter: {counts['bugs']} bugs, {counts['tests']} tests "
                f"({counts['conformance']} conformance, {counts['unit']} unit)",
                file=sys.stderr,
            )
            return 1
        print(
            f"README counts match: {counts['bugs']} bugs, {counts['tests']} tests "
            f"({counts['conformance']} conformance, {counts['unit']} unit)"
        )
        return 0
    if updated != current:
        README.write_text(updated)
        print(
            f"updated README: {counts['bugs']} bugs, {counts['tests']} tests "
            f"({counts['conformance']} conformance, {counts['unit']} unit)"
        )
    else:
        print(
            f"README already current: {counts['bugs']} bugs, {counts['tests']} tests "
            f"({counts['conformance']} conformance, {counts['unit']} unit)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
