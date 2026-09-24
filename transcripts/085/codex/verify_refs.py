#!/usr/bin/env python3
"""Rebuild every externalized request body in the issue 085 Codex captures and
check it against its recorded sha256.

run_codex_matrix.py stores the exact raw bytes of each request's top-level
`instructions` and `tools` values once under shared/ and leaves a
`[kairo-ref sha256:... file:shared/...]` marker in their place. This script is
the inverse, so the committed bodies can be audited byte for byte.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

SPEC = importlib.util.spec_from_file_location(
    "kairo_085_codex_matrix", Path(__file__).with_name("run_codex_matrix.py")
)
matrix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(matrix)


def verify(capture_dirs, shared):
    count = 0
    for directory in capture_dirs:
        for path in sorted(Path(directory).glob("*.jsonl")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                record = json.loads(line)
                for exchange in record["client_exchanges"] + record["upstream_exchanges"]:
                    raw = exchange["request_body_raw"]
                    if raw is None:
                        continue
                    restored = matrix.restore(raw, shared)
                    digest = hashlib.sha256(restored.encode("utf-8")).hexdigest()
                    matrix.require(digest == exchange["request_body_sha256"],
                                   f"{path.name} line {number}: rebuilt body does not match its sha256")
                    json.loads(restored)
                    count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", nargs="+", help="capture directories holding *.jsonl")
    parser.add_argument("--shared", required=True, help="directory holding the externalized values")
    args = parser.parse_args()
    try:
        count = verify(args.captures, Path(args.shared))
    except (OSError, matrix.RunError, json.JSONDecodeError) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"verified {count} request bodies byte for byte")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
