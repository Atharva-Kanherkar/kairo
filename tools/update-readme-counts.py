"""Rewrite README and SCOREBOARD folder/test counts from the repo.

Bugs = unique git-tracked issues/NNN-* writeups.
Tests = #[test] in the harness (conformance + unit).

  python3 tools/update-readme-counts.py          # write both documents
  python3 tools/update-readme-counts.py --check  # fail if either is stale
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SCOREBOARD = ROOT / "issues" / "SCOREBOARD.md"
START = "<!-- kairo-counts:start -->"
END = "<!-- kairo-counts:end -->"

# "The 47 folders cover ..." in the README prose (outside the marker block).
README_PROSE_RE = re.compile(r"(The )\d+( folders cover)")
# "**Coverage**: 48 documented issue folders covering ..." in SCOREBOARD.md.
SCOREBOARD_COVERAGE_RE = re.compile(
    r"(\*\*Coverage\*\*: )\d+( documented issue folders)"
)

GATEWAYS = (
    ("litellm", "LiteLLM"),
    ("switchyard", "NVIDIA Switchyard"),
    ("bifrost", "Bifrost"),
    ("gomodel", "GoModel"),
    ("axonhub", "AxonHub"),
    ("any-llm", "any-llm"),
)

TEST_RE = re.compile(r"^\s*#\[test\]", re.MULTILINE)
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
        f"| Metric | Value |\n"
        f"|---|---|\n"
        f"| Reproduced issue folders | {counts['bugs']} |\n"
        f"| Gateways under test | {gateways} |\n"
        f"| Harness tests | {counts['tests']} ({counts['conformance']} conformance "
        f"checks against recorded transcripts, {counts['unit']} unit) |\n"
        f"{END}"
    )


def apply(text: str, block: str) -> str:
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
    if not pattern.search(text):
        raise SystemExit(
            f"README.md is missing {START} / {END} markers around the Status counts"
        )
    return pattern.sub(block, text, count=1)


def apply_readme_prose(text: str, bugs: int) -> str:
    if not README_PROSE_RE.search(text):
        raise SystemExit(
            "README.md is missing the 'The N folders cover' prose sentence"
        )
    return README_PROSE_RE.sub(rf"\g<1>{bugs}\g<2>", text, count=1)


def apply_scoreboard(text: str, bugs: int) -> str:
    if not SCOREBOARD_COVERAGE_RE.search(text):
        raise SystemExit(
            "issues/SCOREBOARD.md is missing the '**Coverage**: N documented "
            "issue folders' line"
        )
    return SCOREBOARD_COVERAGE_RE.sub(rf"\g<1>{bugs}\g<2>", text, count=1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if README or SCOREBOARD counts do not match the repo",
    )
    args = parser.parse_args()
    counts = collect()
    block = render(counts)

    readme_current = README.read_text()
    readme_updated = apply_readme_prose(apply(readme_current, block), counts["bugs"])
    scoreboard_current = SCOREBOARD.read_text()
    scoreboard_updated = apply_scoreboard(scoreboard_current, counts["bugs"])

    summary = (
        f"{counts['bugs']} bugs, {counts['tests']} tests "
        f"({counts['conformance']} conformance, {counts['unit']} unit)"
    )

    if args.check:
        stale = []
        if readme_updated != readme_current:
            stale.append("README.md")
        if scoreboard_updated != scoreboard_current:
            stale.append("issues/SCOREBOARD.md")
        if stale:
            print(
                f"{', '.join(stale)} count(s) are stale. Run "
                "`python3 tools/update-readme-counts.py` and commit the changes.",
                file=sys.stderr,
            )
            print(f"counter: {summary}", file=sys.stderr)
            return 1
        print(f"README and SCOREBOARD counts match: {summary}")
        return 0

    changed = []
    if readme_updated != readme_current:
        README.write_text(readme_updated)
        changed.append("README.md")
    if scoreboard_updated != scoreboard_current:
        SCOREBOARD.write_text(scoreboard_updated)
        changed.append("issues/SCOREBOARD.md")
    if changed:
        print(f"updated {', '.join(changed)}: {summary}")
    else:
        print(f"README and SCOREBOARD already current: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
