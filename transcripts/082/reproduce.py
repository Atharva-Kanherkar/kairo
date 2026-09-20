#!/usr/bin/env python3
"""Reproduce Bifrost agent-mode same-name result loss through its HTTP API.

By default this writes reviewer-owned evidence to a fresh ignored temporary
directory. Set OUTPUT_DIR to freeze an author run under transcripts/082.
No provider credential is needed or inherited by any child process.
"""

import argparse
import hashlib
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PINNED_COMMIT = "1a17949cd80234de0ceb3018e941854f38b3f7a8"
PINNED_CORE_VERSION = "1.9.1"
EXPECTED_RUNTIME_VERSION = "1.9.1-1a17949c"
MODEL = "mockoai/mimo-v2.5"
RUNTIME_PROVIDER_KEY = "sk-kairo-" + "082-runtime-only"
CELLS = {
    "violation": ["alpha", "beta"],
    "control_distinct": ["alpha", "beta"],
    "control_single": ["alpha"],
}


def compact(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def effect_marker(operation, cell, run):
    return "KAIRO_082_EFFECT_%s_%s_%s" % (
        operation.upper(),
        cell,
        run,
    )


def call_id(operation, cell, run, phase="initial"):
    return "call_082_%s_%s_%s_%s" % (phase, operation, cell, run)


def command_output(command, cwd):
    return subprocess.check_output(command, cwd=cwd, text=True).strip()


def find_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_port(port, process, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("child process exited before opening port %d" % port)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("timed out waiting for port %d" % port)


def wait_gateway(port, process, timeout=45):
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Bifrost exited before /api/version became ready")
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            connection.request("GET", "/api/version")
            response = connection.getresponse()
            body = response.read().decode("utf-8", "replace")
            connection.close()
            if response.status == 200:
                value = json.loads(body)
                return value if isinstance(value, str) else body
        except Exception as error:
            last_error = error
        time.sleep(0.2)
    raise RuntimeError("gateway readiness timed out: %r" % (last_error,))


def raw_request(target, headers, body):
    lines = ["POST %s HTTP/1.1" % target]
    lines.extend("%s: %s" % item for item in headers.items())
    return "\r\n".join(lines) + "\r\n\r\n" + body


def raw_response(status, reason, headers, body):
    lines = ["HTTP/1.1 %d %s" % (status, reason)]
    lines.extend("%s: %s" % item for item in headers)
    return "\r\n".join(lines) + "\r\n\r\n" + body


def post_json(port, payload, request_file, response_file):
    target = "/v1/chat/completions"
    body = compact(payload)
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body.encode())),
        "Host": "127.0.0.1:%d" % port,
    }
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    connection.request("POST", target, body=body, headers=headers)
    response = connection.getresponse()
    response_body = response.read().decode("utf-8", "replace")
    response_headers = response.getheaders()
    connection.close()
    request_file.write_text(raw_request(target, headers, body))
    response_file.write_text(
        raw_response(
            response.status,
            response.reason,
            response_headers,
            response_body,
        )
    )
    if response.status != 200:
        raise AssertionError(
            "gateway returned %d for %s: %s"
            % (response.status, request_file.name, response_body)
        )
    return json.loads(response_body)


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def executions_for(records, cell, run, phase):
    return [
        record
        for record in records
        if record.get("arguments", {}).get("cell") == cell
        and record.get("arguments", {}).get("run") == run
        and record.get("arguments", {}).get("phase") == phase
    ]


def execution_fixture(cell, run, executions):
    return {
        "cell": cell,
        "executions": [
            {
                "effect_marker": record["effect_marker"],
                "tool_call_id": record["tool_call_id"],
            }
            for record in sorted(
                executions, key=lambda item: item["tool_call_id"]
            )
        ],
        "run": run,
    }


def client_message(response):
    choices = response.get("choices") or []
    if len(choices) != 1:
        raise AssertionError("expected one choice, got %d" % len(choices))
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise AssertionError("response has no assistant message")
    return choices[0], message


def build_config(upstream_port, mcp_port, provider_key, config_db):
    return {
        "$schema": "https://www.getbifrost.ai/schema",
        "client": {
            "disable_content_logging": False,
            "enable_logging": True,
            "initial_pool_size": 10,
        },
        "config_store": {
            "config": {"path": str(config_db)},
            "enabled": True,
            "type": "sqlite",
        },
        "logs_store": {"enabled": False},
        "mcp": {
            "client_configs": [
                {
                    "auth_type": "none",
                    "client_id": "kairo-side-effect-082",
                    "connection_string": "http://127.0.0.1:%d/mcp" % mcp_port,
                    "connection_type": "http",
                    "is_ping_available": False,
                    "name": "KairoSideEffect",
                    "needs_session_stickiness": True,
                    "tools_to_auto_execute": ["charge", "credit"],
                    "tools_to_execute": ["*"],
                }
            ],
            "tool_manager_config": {
                "max_agent_depth": 5,
                "tool_execution_timeout": "5s",
            },
        },
        "providers": {
            "mockoai": {
                "custom_provider_config": {"base_provider_type": "openai"},
                "keys": [
                    {
                        "models": ["*"],
                        "name": "kairo-synthetic",
                        "value": provider_key,
                        "weight": 1,
                    }
                ],
                "network_config": {
                    "base_url": "http://127.0.0.1:%d" % upstream_port
                },
            }
        },
    }


def safe_child_env(extra, temp_dir):
    child = {
        "HOME": str(temp_dir),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(temp_dir),
    }
    child.update(extra)
    return child


def stop_process(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def scan_sanitized(output_dir):
    forbidden = [RUNTIME_PROVIDER_KEY]
    for file_path in output_dir.rglob("*"):
        if not file_path.is_file():
            continue
        data = file_path.read_bytes()
        for needle in forbidden:
            if needle.encode() in data:
                raise AssertionError(
                    "unsanitized runtime marker found in %s" % file_path
                )


def run(args):
    source = Path(args.source).resolve()
    binary = Path(args.binary).resolve()
    if not source.is_dir():
        raise RuntimeError("BIFROST_SOURCE is not a directory")
    if not binary.is_file():
        raise RuntimeError("BIFROST_BIN is not a file")

    commit = command_output(["git", "rev-parse", "HEAD"], source)
    if commit != PINNED_COMMIT:
        raise AssertionError("expected Bifrost %s, got %s" % (PINNED_COMMIT, commit))
    core_version = (source / "core" / "version").read_text().strip()
    if core_version != PINNED_CORE_VERSION:
        raise AssertionError(
            "expected core version %s, got %s"
            % (PINNED_CORE_VERSION, core_version)
        )
    subprocess.check_call(
        [
            "git",
            "diff",
            "--exit-code",
            "HEAD",
            "--",
            "core/mcp/agent.go",
            "core/mcp/agentadaptors.go",
        ],
        cwd=source,
        stdout=subprocess.DEVNULL,
    )

    if args.output:
        output_dir = Path(args.output).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = Path(tempfile.mkdtemp(prefix="kairo-082-review-"))
    runs_dir = output_dir / "runs"
    upstream_capture = output_dir / "upstream"
    mcp_capture = output_dir / "mcp"
    for directory in [runs_dir, upstream_capture, mcp_capture]:
        directory.mkdir(parents=True, exist_ok=True)

    script_dir = Path(__file__).resolve().parent
    upstream_port = find_port()
    mcp_port = find_port()
    gateway_port = find_port()
    runtime_dir = Path(tempfile.mkdtemp(prefix="kairo-082-runtime-"))
    config = build_config(
        upstream_port,
        mcp_port,
        RUNTIME_PROVIDER_KEY,
        runtime_dir / "config.db",
    )
    (runtime_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    sanitized_config = build_config(
        upstream_port,
        mcp_port,
        "<SYNTHETIC_PROVIDER_KEY>",
        "<RUNTIME_DIR>/config.db",
    )
    (output_dir / "config.sanitized.json").write_text(
        json.dumps(sanitized_config, indent=2) + "\n"
    )

    target = {
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "commit": commit,
        "core_version": core_version,
        "go_version": command_output(["go", "version"], source).removeprefix(
            "go version "
        ),
        "model": MODEL,
        "public_endpoint": "POST /v1/chat/completions",
        "request_dialect": "OpenAI Chat Completions",
        "response_dialect": "OpenAI Chat Completions",
        "source": "https://github.com/maximhq/bifrost",
        "ui_embed_stub": "transports/bifrost-http/ui/index.html; not on the request path",
    }
    (output_dir / "target.json").write_text(json.dumps(target, indent=2) + "\n")

    logs = []
    processes = []
    upstream_process = None
    mcp_process = None
    gateway_process = None
    results = {
        "complete": False,
        "runs": args.runs,
        "target": target,
        "cells": {},
        "consumer": {},
    }
    try:
        upstream_log = (output_dir / "upstream-server.log").open("w")
        mcp_log = (output_dir / "mcp-server.log").open("w")
        gateway_log = (output_dir / "bifrost.log").open("w")
        logs.extend([upstream_log, mcp_log, gateway_log])
        upstream_process = subprocess.Popen(
            [sys.executable, str(script_dir / "mock_upstream.py")],
            env=safe_child_env(
                {
                    "CAPTURE_DIR": str(upstream_capture),
                    "PORT": str(upstream_port),
                    "PYTHONUNBUFFERED": "1",
                },
                runtime_dir,
            ),
            stdout=upstream_log,
            stderr=subprocess.STDOUT,
        )
        processes.append(upstream_process)
        mcp_process = subprocess.Popen(
            [sys.executable, str(script_dir / "mcp_server.py")],
            env=safe_child_env(
                {
                    "CAPTURE_DIR": str(mcp_capture),
                    "PORT": str(mcp_port),
                    "PYTHONUNBUFFERED": "1",
                },
                runtime_dir,
            ),
            stdout=mcp_log,
            stderr=subprocess.STDOUT,
        )
        processes.append(mcp_process)
        wait_port(upstream_port, upstream_process)
        wait_port(mcp_port, mcp_process)

        gateway_process = subprocess.Popen(
            [
                str(binary),
                "-app-dir",
                str(runtime_dir),
                "-host",
                "127.0.0.1",
                "-log-level",
                "debug",
                "-log-style",
                "json",
                "-port",
                str(gateway_port),
            ],
            cwd=source,
            env=safe_child_env({}, runtime_dir),
            stdout=gateway_log,
            stderr=subprocess.STDOUT,
        )
        processes.append(gateway_process)
        runtime_version = wait_gateway(gateway_port, gateway_process)
        if runtime_version != EXPECTED_RUNTIME_VERSION:
            raise AssertionError(
                "expected runtime version %s, got %r"
                % (EXPECTED_RUNTIME_VERSION, runtime_version)
            )
        results["target"]["runtime_version"] = runtime_version

        first_responses = {}
        for cell, operations in CELLS.items():
            cell_results = []
            for iteration in range(1, args.runs + 1):
                run_id = "%02d" % iteration
                request_file = runs_dir / ("%s-%s-client-request.http" % (cell, run_id))
                response_file = runs_dir / ("%s-%s-client-response.http" % (cell, run_id))
                response = post_json(
                    gateway_port,
                    {
                        "messages": [
                            {
                                "content": "KAIRO_082_FIRST cell=%s run=%s"
                                % (cell, run_id),
                                "role": "user",
                            }
                        ],
                        "model": MODEL,
                        "stream": False,
                        "temperature": 0,
                    },
                    request_file,
                    response_file,
                )
                choice, message = client_message(response)
                content = message.get("content") or ""
                expected_markers = [
                    effect_marker(operation, cell, run_id)
                    for operation in operations
                ]
                reported = [marker for marker in expected_markers if marker in content]
                all_executions = read_jsonl(mcp_capture / "executions.jsonl")
                executed = executions_for(all_executions, cell, run_id, "initial")
                executed_markers = sorted(
                    record.get("effect_marker") for record in executed
                )
                pending = message.get("tool_calls") or []
                expected_count = 1 if cell == "violation" else len(operations)
                if len(executed) != len(operations):
                    raise AssertionError(
                        "%s %s executed %d tools, expected %d"
                        % (cell, run_id, len(executed), len(operations))
                    )
                if sorted(expected_markers) != executed_markers:
                    raise AssertionError("MCP execution markers do not match the turn")
                if len(reported) != expected_count:
                    raise AssertionError(
                        "%s %s reported %d results, expected %d"
                        % (cell, run_id, len(reported), expected_count)
                    )
                if len(pending) != 1 or not pending[0].get("function", {}).get(
                    "name", ""
                ).endswith("-review"):
                    raise AssertionError("mixed turn lost its manual review call")
                if choice.get("finish_reason") != "stop":
                    raise AssertionError("mixed turn did not stop for manual approval")
                cell_results.append(
                    {
                        "executed_count": len(executed),
                        "executed_markers": executed_markers,
                        "pending_manual_count": len(pending),
                        "reported_count": len(reported),
                        "reported_markers": reported,
                        "run": run_id,
                    }
                )
                first_responses[(cell, run_id)] = response
                if iteration == 1:
                    shutil.copyfile(
                        request_file,
                        output_dir / ("%s-client-request.http" % cell),
                    )
                    shutil.copyfile(
                        response_file,
                        output_dir / ("%s-client-response.http" % cell),
                    )
                    (output_dir / ("%s-executions.json" % cell)).write_text(
                        json.dumps(
                            execution_fixture(cell, run_id, executed),
                            indent=2,
                        )
                        + "\n"
                    )
            results["cells"][cell] = {
                "executions_per_run": [item["executed_count"] for item in cell_results],
                "reported_results_per_run": [
                    item["reported_count"] for item in cell_results
                ],
                "runs": cell_results,
            }

        for cell in ["violation", "control_distinct"]:
            consumer_results = []
            for iteration in range(1, args.runs + 1):
                run_id = "%02d" % iteration
                first_response = first_responses[(cell, run_id)]
                _, assistant = client_message(first_response)
                pending = assistant.get("tool_calls") or []
                if len(pending) != 1:
                    raise AssertionError("consumer setup needs one pending review call")
                request_file = runs_dir / (
                    "%s-%s-consumer-request.http" % (cell, run_id)
                )
                response_file = runs_dir / (
                    "%s-%s-consumer-response.http" % (cell, run_id)
                )
                response = post_json(
                    gateway_port,
                    {
                        "messages": [
                            {
                                "content": "KAIRO_082_FIRST cell=%s run=%s"
                                % (cell, run_id),
                                "role": "user",
                            },
                            assistant,
                            {
                                "content": "manual review rejected for synthetic test",
                                "role": "tool",
                                "tool_call_id": pending[0]["id"],
                            },
                            {
                                "content": "KAIRO_082_CONSUMER cell=%s run=%s"
                                % (cell, run_id),
                                "role": "user",
                            },
                        ],
                        "model": MODEL,
                        "stream": False,
                        "temperature": 0,
                    },
                    request_file,
                    response_file,
                )
                _, final_message = client_message(response)
                final_content = final_message.get("content") or ""
                if "KAIRO_082_CONSUMER_COMPLETE" not in final_content:
                    raise AssertionError("consumer replay did not reach its final turn")
                all_executions = read_jsonl(mcp_capture / "executions.jsonl")
                duplicates = executions_for(
                    all_executions, cell, run_id, "consumer"
                )
                expected_duplicates = 1 if cell == "violation" else 0
                if len(duplicates) != expected_duplicates:
                    raise AssertionError(
                        "%s %s duplicated %d side effects, expected %d"
                        % (cell, run_id, len(duplicates), expected_duplicates)
                    )
                consumer_results.append(
                    {
                        "duplicate_executions": len(duplicates),
                        "run": run_id,
                    }
                )
                if iteration == 1:
                    shutil.copyfile(
                        request_file,
                        output_dir / ("%s-consumer-request.http" % cell),
                    )
                    shutil.copyfile(
                        response_file,
                        output_dir / ("%s-consumer-response.http" % cell),
                    )
            results["consumer"][cell] = {
                "duplicate_executions_per_run": [
                    item["duplicate_executions"] for item in consumer_results
                ],
                "runs": consumer_results,
            }

        results["complete"] = True
        (output_dir / "results.json").write_text(
            json.dumps(results, indent=2, sort_keys=True) + "\n"
        )
        scan_sanitized(output_dir)
        print(json.dumps(results, indent=2, sort_keys=True))
        print("evidence: %s" % output_dir)
        return 0
    finally:
        for process in reversed(processes):
            stop_process(process)
        for log in logs:
            log.close()
        shutil.rmtree(runtime_dir, ignore_errors=True)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source", default=os.environ.get("BIFROST_SOURCE"), required=False
    )
    parser.add_argument(
        "--binary", default=os.environ.get("BIFROST_BIN"), required=False
    )
    parser.add_argument("--output", default=os.environ.get("OUTPUT_DIR"))
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    if not args.source or not args.binary:
        parser.error("set BIFROST_SOURCE and BIFROST_BIN or pass both flags")
    if args.runs < 1:
        parser.error("--runs must be positive")
    return args


if __name__ == "__main__":
    try:
        sys.exit(run(parse_args()))
    except Exception as error:
        print("reproduction failed: %s" % error, file=sys.stderr)
        raise
