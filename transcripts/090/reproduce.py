#!/usr/bin/env python3
"""Capture OpenShell finding 090 policy-merge trials through the public CLI."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


CASES = {
    "failing": "192.168.65.254:19100:full:websocket:enforce:allow-uninspected-credentials,allowed-ip=192.168.65.254",
    "trigger-removed": "192.168.65.254:19100:full:websocket:enforce:allowed-ip=192.168.65.254",
    "sibling": "192.168.65.254:19100:full:websocket:enforce:websocket-credential-rewrite,allowed-ip=192.168.65.254",
}


def command_record(args: list[str]) -> list[str]:
    normalized = list(args)
    normalized[0] = "$OPENSHELL_BIN"
    for index, value in enumerate(normalized):
        if value == "--policy" and index + 1 < len(normalized):
            normalized[index + 1] = "$SEED_POLICY"
    return normalized


def run_command(args: list[str], run_dir: Path, name: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(args, text=True, capture_output=True, check=False)
    (run_dir / f"{name}.stdout.txt").write_text(completed.stdout)
    (run_dir / f"{name}.stderr.txt").write_text(completed.stderr)
    (run_dir / f"{name}.command.json").write_text(
        json.dumps(
            {"argv": command_record(args), "exit_code": completed.returncode},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return completed


def get_policy(args: argparse.Namespace, run_dir: Path, name: str) -> dict[str, object]:
    command = [
        str(args.openshell),
        "--color",
        "never",
        "--gateway",
        args.gateway,
        "policy",
        "get",
        args.sandbox,
        "--base",
        "--output",
        "json",
    ]
    completed = run_command(command, run_dir, name)
    if completed.returncode != 0:
        raise RuntimeError(f"{name} failed: {completed.stderr}")
    return json.loads(completed.stdout)


def realtime(policy_response: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    policy = policy_response["policy"]
    assert isinstance(policy, dict)
    network_policies = policy["network_policies"]
    assert isinstance(network_policies, dict)
    rule = network_policies["realtime"]
    assert isinstance(rule, dict)
    endpoints = rule["endpoints"]
    binaries = rule["binaries"]
    assert isinstance(endpoints, list) and len(endpoints) == 1
    assert isinstance(binaries, list)
    endpoint = endpoints[0]
    assert isinstance(endpoint, dict)
    paths = [binary["path"] for binary in binaries]
    return endpoint, paths


def run_trial(args: argparse.Namespace, case: str, trial: int) -> dict[str, object]:
    run_dir = args.output_dir / case / f"run-{trial:02d}"
    run_dir.mkdir(parents=True)

    reset = run_command(
        [
            str(args.openshell),
            "--color",
            "never",
            "--gateway",
            args.gateway,
            "policy",
            "set",
            args.sandbox,
            "--policy",
            str(args.seed_policy),
        ],
        run_dir,
        "reset",
    )
    if reset.returncode != 0:
        raise RuntimeError(f"reset failed: {reset.stderr}")

    before = get_policy(args, run_dir, "before")
    before_endpoint, before_binaries = realtime(before)
    update = run_command(
        [
            str(args.openshell),
            "--color",
            "never",
            "--gateway",
            args.gateway,
            "policy",
            "update",
            args.sandbox,
            "--rule-name",
            "realtime",
            "--binary",
            "/usr/local/bin/tool-a",
            "--add-endpoint",
            CASES[case],
        ],
        run_dir,
        "update",
    )
    after = get_policy(args, run_dir, "after")
    after_endpoint, after_binaries = realtime(after)

    before_flag = bool(before_endpoint.get("allow_uninspected_credentials", False))
    after_flag = bool(after_endpoint.get("allow_uninspected_credentials", False))
    b_present = "/usr/local/bin/tool-b" in after_binaries
    b_declared_by_update = False

    if case == "failing":
        passed = update.returncode == 0 and not before_flag and after_flag and b_present
    elif case == "trigger-removed":
        passed = update.returncode == 0 and not before_flag and not after_flag and b_present
    else:
        normalized_error = " ".join(update.stderr.split())
        passed = (
            update.returncode != 0
            and not before_flag
            and not after_flag
            and b_present
            and "existing binaries" in normalized_error
            and "also" in normalized_error
            and "declare" in normalized_error
            and "tool-b" in normalized_error
        )

    result = {
        "case": case,
        "trial": trial,
        "passed": passed,
        "update_exit_code": update.returncode,
        "before_flag": before_flag,
        "after_flag": after_flag,
        "before_binaries": before_binaries,
        "after_binaries": after_binaries,
        "binary_b_present_after": b_present,
        "binary_b_declared_by_update": b_declared_by_update,
        "artifacts": {
            "before": str((run_dir / "before.stdout.txt").relative_to(args.output_dir)),
            "update_command": str((run_dir / "update.command.json").relative_to(args.output_dir)),
            "update_stdout": str((run_dir / "update.stdout.txt").relative_to(args.output_dir)),
            "update_stderr": str((run_dir / "update.stderr.txt").relative_to(args.output_dir)),
            "after": str((run_dir / "after.stdout.txt").relative_to(args.output_dir)),
        },
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if not passed:
        raise RuntimeError(f"{case} trial {trial} violated its expected invariant")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--openshell", type=Path, required=True)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--sandbox", required=True)
    parser.add_argument("--seed-policy", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--target-commit", required=True)
    parser.add_argument("--trials", type=int, default=5)
    args = parser.parse_args()

    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite {args.output_dir}")
    args.output_dir.mkdir(parents=True)

    version = subprocess.run(
        [str(args.openshell), "--version"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    metadata = {
        "schema_version": 1,
        "target": {
            "name": "NVIDIA/OpenShell",
            "version": args.target_version,
            "commit": args.target_commit,
            "cli_version_output": version,
        },
        "gateway": args.gateway,
        "sandbox": args.sandbox,
        "trials_per_case": args.trials,
        "environment_names": sorted(
            name for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME") if name in os.environ
        ),
    }
    (args.output_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    )

    trials = [
        run_trial(args, case, trial)
        for case in CASES
        for trial in range(1, args.trials + 1)
    ]
    summary = {
        "schema_version": 1,
        "target": metadata["target"],
        "trials_per_case": args.trials,
        "cases": {
            case: {
                "passed": sum(1 for trial in trials if trial["case"] == case and trial["passed"]),
                "total": sum(1 for trial in trials if trial["case"] == case),
            }
            for case in CASES
        },
        "trials": trials,
    }
    (args.output_dir / "results.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary["cases"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
