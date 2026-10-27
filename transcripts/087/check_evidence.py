#!/usr/bin/env python3
"""Fail-closed checker for finding 087 evidence matrices."""

import argparse
import importlib.util
import json
from pathlib import Path
import re

MODULE_PATH = Path(__file__).with_name("reproduce.py")
SPEC = importlib.util.spec_from_file_location("kairo_087_reproduce", MODULE_PATH)
reproduce = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reproduce)

FORBIDDEN_DASHES = ("\N{EM DASH}", "\N{EN DASH}")
LOCAL_PATH = re.compile(r"/(?:Users|home|private|tmp|var/folders)/[^\s\"']+")


def require(condition, message):
    if not condition:
        raise reproduce.ReproductionError(message)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise reproduce.ReproductionError("cannot read valid JSON from %s" % path.name) from error


def check_raw_run(name, spec, run, run_dir):
    request = (run_dir / "client-request.http").read_text(encoding="utf-8")
    response = (run_dir / "client-response.http").read_text(encoding="utf-8")
    require(request.startswith("POST /v1/chat/completions HTTP/1.1\n") or
            request.startswith("POST /v1/chat/completions HTTP/1.1\r\n"),
            name + ": wrong public request route")
    require('"type":"mcp"' in request, name + ": client request does not use MCP")
    require('"require_approval":"never"' in request, name + ": approval mode changed")
    expected_status = 200 if name == "control_fault_off" else 500
    require(response.startswith("HTTP/1.1 %d " % expected_status), name + ": raw client status changed")
    require(run.get("client_status") == expected_status, name + ": summary client status changed")

    metadata = sorted((run_dir / "upstream").glob("upstream-*.json"))
    records = [read_json(path) for path in metadata]
    kinds = [record.get("kind") for record in records]
    if spec["fault"] == "off":
        require(kinds == ["initial", "followup-ok"], name + ": healthy control call order changed")
    else:
        attempts = spec["expected_tools"]
        require(kinds == [kind for _ in range(attempts) for kind in ("initial", "followup-fault")],
                name + ": faulting retry call order changed")
    require(all(record.get("path") == "/v1/chat/completions" for record in records),
            name + ": forwarded route changed")
    require(sum(record.get("followup") is True for record in records) == spec["expected_tools"],
            name + ": follow-up count differs from tool count")


def scan_artifacts(root, results_path):
    paths = [results_path]
    paths.extend(path for path in (root / "cells").rglob("*") if path.is_file())
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        require(not reproduce.CREDENTIAL_PATTERN.search(text), "%s contains a credential-shaped token" % path.name)
        require(not LOCAL_PATH.search(text), "%s contains an absolute local path" % path.name)
        require(not any(dash in text for dash in FORBIDDEN_DASHES), "%s contains a forbidden dash" % path.name)


def check_matrix(results_path, evidence_root):
    summary = read_json(results_path)
    require(summary.get("complete") is True, "matrix is not marked complete")
    require(summary.get("failures") == [], "matrix records failures")
    reproduce.validate_matrix(summary, evidence_root)
    for name, spec in reproduce.CELLS.items():
        for run in summary["cells"][name]["runs"]:
            run_dir = evidence_root / "cells" / name / "runs" / ("run-%s" % run["run"])
            check_raw_run(name, spec, run, run_dir)
    scan_artifacts(evidence_root, results_path)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    args = parser.parse_args()
    root = args.evidence_root or args.results.parent
    try:
        summary = check_matrix(args.results, root)
    except (OSError, UnicodeDecodeError, reproduce.ReproductionError) as error:
        print("FAIL: %s" % error)
        return 1
    print("PASS: LiteLLM %s, four complete 5/5 cells" % summary["target"]["litellm_version"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
