#!/usr/bin/env python3
"""Keep only non-test, non-doc files from a unified diff (for building gold.patch from an upstream PR).

  gh pr diff 1312 -R mozilla-ai/any-llm | scripts/source-only-patch.py > tasks/<id>/gold.patch

The upstream PR's own tests are left out on purpose: grading uses the task's
implementation-agnostic hidden verifier, and the base-commit test files are
restored before it runs.
"""

import re
import sys

sys.path.insert(0, __file__.rsplit("/", 2)[0])
from kairo_bench.paths import is_test_path  # noqa: E402

text = sys.stdin.read()
chunks = re.split(r"(?m)^(?=diff --git )", text)
kept = []
for chunk in chunks:
    m = re.match(r"diff --git a/(\S+) b/(\S+)", chunk)
    if not m:
        continue
    path = m.group(2)
    lowered = path.lower()
    if lowered.endswith((".md", ".mdx", ".rst")) or lowered.startswith("docs/") or "/e2e/" in lowered:
        print(f"dropped {path} (docs)", file=sys.stderr)
        continue
    if is_test_path(path):
        print(f"dropped {path}", file=sys.stderr)
        continue
    kept.append(chunk)
sys.stdout.write("".join(kept))
