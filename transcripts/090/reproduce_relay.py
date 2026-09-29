#!/usr/bin/env python3
"""Run the live binary-B WebSocket consequence from a Ready sandbox."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


PAYLOAD_HEX = b"KAIRO-090-BINARY-ACTION".hex()
FLAG_SPEC = (
    "192.168.65.254:19100:full:websocket:enforce:"
    "allow-uninspected-credentials,allowed-ip=192.168.65.254"
)


def normalize_command(command: list[str], seed_policy: Path) -> list[str]:
    normalized = list(command)
    normalized[0] = "$OPENSHELL_BIN"
    for index, value in enumerate(normalized):
        if value == str(seed_policy):
            normalized[index] = "$SEED_POLICY"
    return normalized


def run_cli(
    command: list[str], run_dir: Path, name: str, seed_policy: Path
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    (run_dir / f"{name}.stdout.txt").write_text(completed.stdout)
    (run_dir / f"{name}.stderr.txt").write_text(completed.stderr)
    (run_dir / f"{name}.command.json").write_text(
        json.dumps(
            {
                "argv": normalize_command(command, seed_policy),
                "exit_code": completed.returncode,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return completed


def prefix(args: argparse.Namespace) -> list[str]:
    return [str(args.openshell), "--color", "never", "--gateway", args.gateway]


def reset_policy(args: argparse.Namespace, run_dir: Path) -> None:
    completed = run_cli(
        prefix(args)
        + [
            "policy",
            "set",
            args.sandbox,
            "--policy",
            str(args.seed_policy),
            "--wait",
            "--timeout",
            "30",
        ],
        run_dir,
        "reset",
        args.seed_policy,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr)


def enable_flag(args: argparse.Namespace, run_dir: Path) -> None:
    completed = run_cli(
        prefix(args)
        + [
            "policy",
            "update",
            args.sandbox,
            "--rule-name",
            "realtime",
            "--binary",
            "/usr/local/bin/tool-a",
            "--add-endpoint",
            FLAG_SPEC,
            "--wait",
            "--timeout",
            "30",
        ],
        run_dir,
        "enable",
        args.seed_policy,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr)


def capture_policy(args: argparse.Namespace, run_dir: Path) -> None:
    completed = run_cli(
        prefix(args)
        + [
            "policy",
            "get",
            args.sandbox,
            "--base",
            "--output",
            "json",
        ],
        run_dir,
        "effective-policy",
        args.seed_policy,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr)


def run_trial(args: argparse.Namespace, mode: str, trial: int) -> dict[str, object]:
    run_dir = args.output_dir / mode / f"run-{trial:02d}"
    run_dir.mkdir(parents=True)
    reset_policy(args, run_dir)
    if mode == "flag-on":
        enable_flag(args, run_dir)
    capture_policy(args, run_dir)

    ready_file = run_dir / "server.ready"
    server_dir = run_dir / "upstream"
    server = subprocess.Popen(
        [
            str(args.python),
            str(args.server_script),
            "--output-dir",
            str(server_dir),
            "--connections",
            "1",
            "--ready-file",
            str(ready_file),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    for _ in range(100):
        if ready_file.exists():
            break
        if server.poll() is not None:
            stdout, stderr = server.communicate()
            raise RuntimeError(f"server exited early: {stdout}\n{stderr}")
        time.sleep(0.05)
    else:
        server.terminate()
        raise RuntimeError("server did not become ready")

    client = run_cli(
        prefix(args)
        + [
            "sandbox",
            "exec",
            "--name",
            args.sandbox,
            "--no-tty",
            "--no-login-shell",
            "--",
            "/usr/local/bin/tool-b",
            args.client_path,
        ],
        run_dir,
        "client",
        args.seed_policy,
    )
    try:
        server_stdout, server_stderr = server.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        server.terminate()
        server_stdout, server_stderr = server.communicate(timeout=5)
        raise RuntimeError("server timed out")
    (run_dir / "server.stdout.txt").write_text(server_stdout)
    (run_dir / "server.stderr.txt").write_text(server_stderr)
    if server.returncode != 0:
        raise RuntimeError(f"server failed: {server_stderr}")
    if client.returncode != 0:
        raise RuntimeError(f"client failed: {client.stderr}")

    client_result = json.loads(client.stdout)
    upstream_result_path = server_dir / "connection-001" / "result.json"
    upstream_result = json.loads(upstream_result_path.read_text())
    if mode == "flag-off":
        passed = (
            client_result.get("outcome") == "closed"
            and client_result.get("close_code") == 1008
            and upstream_result.get("outcome") == "no-upstream-frame"
            and upstream_result.get("authorization_present") is True
        )
    else:
        passed = (
            client_result.get("outcome") == "forwarded"
            and client_result.get("byte_equal") is True
            and client_result.get("sent_hex") == PAYLOAD_HEX
            and client_result.get("received_hex") == PAYLOAD_HEX
            and upstream_result.get("outcome") == "binary-forwarded"
            and upstream_result.get("payload_hex") == PAYLOAD_HEX
            and upstream_result.get("authorization_present") is True
        )
    result = {
        "mode": mode,
        "trial": trial,
        "passed": passed,
        "binary": "/usr/local/bin/tool-b",
        "binary_declared_by_update": False,
        "client": client_result,
        "upstream": upstream_result,
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if not passed:
        raise RuntimeError(f"{mode} trial {trial} failed")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--openshell", type=Path, required=True)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--sandbox", required=True)
    parser.add_argument("--seed-policy", type=Path, required=True)
    parser.add_argument("--server-script", type=Path, required=True)
    parser.add_argument("--client-path", default="/sandbox/ws_client.py")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--target-commit", required=True)
    parser.add_argument("--python", type=Path, default=Path("python3"))
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()

    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite {args.output_dir}")
    args.output_dir.mkdir(parents=True)

    results = [
        run_trial(args, mode, trial)
        for mode in ("flag-off", "flag-on")
        for trial in range(1, args.trials + 1)
    ]
    summary = {
        "schema_version": 1,
        "target": {
            "name": "NVIDIA/OpenShell",
            "version": args.target_version,
            "commit": args.target_commit,
        },
        "sandbox": args.sandbox,
        "trials_per_mode": args.trials,
        "modes": {
            mode: {
                "passed": sum(1 for item in results if item["mode"] == mode and item["passed"]),
                "total": sum(1 for item in results if item["mode"] == mode),
            }
            for mode in ("flag-off", "flag-on")
        },
        "results": results,
    }
    (args.output_dir / "results.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary["modes"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
