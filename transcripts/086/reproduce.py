#!/usr/bin/env python3
"""Reproduce kairo issue 086: Bifrost direct cache serves a Chat Completions
entry to a Responses request, and the reverse, through the public HTTP API.

The runner builds nothing. It checks the pinned Bifrost checkout and binary,
starts a deterministic OpenAI-compatible upstream, an optional local Qdrant
vector store, and the gateway with the documented direct-only
`semantic_cache` configuration, then drives the public routes.

By default evidence goes to a fresh temporary directory. Set OUTPUT_DIR to
freeze an author run. No provider credential is needed or inherited by any
child process.
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

PINS = {
    "411d62b28b03b03bd3b4025b2cfab50af45f05f4": {
        "label": "transports/v2.2.3",
        "runtime_version": "v2.2.3",
    },
    "cefde78ba0e574f03da111e3fdcb9f4286e5ccc4": {
        "label": "dev cefde78b",
        "runtime_version": "dev-cefde78b",
    },
}
MODEL = "openai/gpt-4o-mini"
RUNTIME_PROVIDER_KEY = "sk-kairo-" + "086-runtime-only"
PROMPT = "KAIRO_086 cell=%s run=%s Reply with the capital of France."

# cell -> (first call, second call, send cache key, expected upstream calls)
CELLS = {
    "chat_then_responses": ("chat", "responses", True),
    "responses_then_chat": ("responses", "chat", True),
    "chat_stream_then_responses_stream": ("chat_stream", "responses_stream", True),
    "responses_stream_then_chat_stream": ("responses_stream", "chat_stream", True),
    "control_chat_then_chat": ("chat", "chat", True),
    "control_responses_then_responses": ("responses", "responses", True),
    "control_no_cache_key": ("chat", "responses", False),
    "control_typed_responses_item": ("chat", "responses_typed", True),
}
VIOLATION_CELLS = [name for name in CELLS if not name.startswith("control_")]


def compact(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def command_output(command, cwd):
    return subprocess.check_output(command, cwd=cwd, text=True).strip()


def find_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_port(port, process, timeout=30):
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


def wait_http_ok(port, path, process, timeout=60):
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("process exited before %s became ready" % path)
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            connection.request("GET", path)
            response = connection.getresponse()
            body = response.read().decode("utf-8", "replace")
            connection.close()
            if response.status == 200:
                return body
        except Exception as error:  # noqa: BLE001 - readiness polling
            last_error = error
        time.sleep(0.2)
    raise RuntimeError("readiness timed out for %s: %r" % (path, last_error))


def request_payload(kind, cell, run):
    prompt = PROMPT % (cell, run)
    if kind in ("chat", "chat_stream"):
        payload = {"model": MODEL, "messages": [{"role": "user", "content": prompt}]}
        target = "/v1/chat/completions"
    elif kind in ("responses", "responses_stream"):
        payload = {"model": MODEL, "input": prompt}
        target = "/v1/responses"
    elif kind == "responses_typed":
        payload = {
            "model": MODEL,
            "input": [{"type": "message", "role": "user", "content": prompt}],
        }
        target = "/v1/responses"
    else:
        raise ValueError(kind)
    if kind.endswith("_stream"):
        payload["stream"] = True
    return target, payload


def raw_request(target, headers, body):
    lines = ["POST %s HTTP/1.1" % target]
    lines.extend("%s: %s" % item for item in headers.items())
    return "\r\n".join(lines) + "\r\n\r\n" + body


def raw_response(status, reason, headers, body):
    lines = ["HTTP/1.1 %d %s" % (status, reason)]
    lines.extend("%s: %s" % item for item in headers)
    return "\r\n".join(lines) + "\r\n\r\n" + body


def post(port, target, payload, cache_key, request_file, response_file):
    body = compact(payload)
    headers = {
        "Host": "127.0.0.1:%d" % port,
        "Content-Type": "application/json",
        "Content-Length": str(len(body.encode())),
    }
    if cache_key:
        headers["x-bf-cache-key"] = cache_key
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    connection.request("POST", target, body=body, headers=headers)
    response = connection.getresponse()
    response_body = response.read().decode("utf-8", "replace")
    response_headers = response.getheaders()
    connection.close()
    request_file.write_text(raw_request(target, headers, body))
    response_file.write_text(
        raw_response(response.status, response.reason, response_headers, response_body)
    )
    return response.status, dict((k.lower(), v) for k, v in response_headers), response_body


def classify(kind, status, headers, body):
    """Name the response family the client actually received."""
    result = {"status": status, "content_type": headers.get("content-type", "")}
    if kind.endswith("_stream"):
        events = []
        data_objects = []
        done = False
        for line in body.splitlines():
            if line.startswith("event: "):
                events.append(line[len("event: "):])
            elif line.startswith("data: "):
                data = line[len("data: "):]
                if data == "[DONE]":
                    done = True
                    continue
                try:
                    value = json.loads(data)
                except ValueError:
                    data_objects.append("unparseable")
                    continue
                if isinstance(value, dict):
                    data_objects.append(value.get("type") or value.get("object") or "untyped")
        result["sse_event_names"] = sorted(set(events))
        result["data_kinds"] = sorted(set(data_objects))
        result["done_marker"] = done
        result["response_completed"] = "response.completed" in data_objects
        if data_objects and all(kind_ == "chat.completion.chunk" for kind_ in data_objects):
            result["family"] = "chat.completion.chunk"
        elif data_objects and all(str(kind_).startswith("response.") for kind_ in data_objects):
            result["family"] = "responses-events"
        else:
            result["family"] = "mixed-or-empty"
        return result
    try:
        value = json.loads(body)
    except ValueError:
        result["family"] = "unparseable"
        return result
    if value is None:
        result["family"] = "null"
    elif isinstance(value, dict) and value.get("object") == "chat.completion":
        result["family"] = "chat.completion"
        result["id"] = value.get("id")
    elif isinstance(value, dict) and value.get("object") == "response":
        result["family"] = "response"
        result["id"] = value.get("id")
    else:
        result["family"] = "other"
    return result


EXPECTED_FAMILY = {
    "chat": "chat.completion",
    "responses": "response",
    "responses_typed": "response",
    "chat_stream": "chat.completion.chunk",
    "responses_stream": "responses-events",
}


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def warm_up(port, upstream_dir, runs_dir, attempts=20):
    """Wait until the store serves a same-family hit before measuring.

    The first write into a fresh vector store can land after a short fixed
    wait. This unmeasured pair uses its own cache key and proves the cache is
    live, so the first matrix run is not a cold-store miss.
    """
    for attempt in range(1, attempts + 1):
        run_id = "%02d" % attempt
        target_path, payload = request_payload("chat", "warmup", run_id)
        for phase in ("first", "probe"):
            stem = "warmup-%s-%s" % (run_id, phase)
            post(
                port,
                target_path,
                payload,
                "kairo-086-warmup",
                runs_dir / (stem + "-client-request.http"),
                runs_dir / (stem + "-client-response.http"),
            )
            if phase == "first":
                time.sleep(1.0)
        calls = [
            record
            for record in read_jsonl(upstream_dir / "upstream.jsonl")
            if record.get("cell") == "warmup" and record.get("run") == run_id
        ]
        if len(calls) == 1:
            return {"attempts": attempt, "same_family_hit": True}
    raise RuntimeError("cache never served a same-family hit during warm-up")


PERSISTENCE_CELL = "persistence_chat_then_responses"
PERSISTENCE_REPEATS = 3


def measure_persistence(port, upstream_dir, runs_dir, args):
    """After one chat call, repeat the same Responses call several times.

    A client that retries or a second service that asks later keeps getting
    the wrong family for as long as the entry lives; the upstream is never
    called again.
    """
    runs = []
    for iteration in range(1, args.runs + 1):
        run_id = "%02d" % iteration
        cache_key = "kairo-086-%s-%s" % (PERSISTENCE_CELL, run_id)
        target_path, payload = request_payload("chat", PERSISTENCE_CELL, run_id)
        stem = "%s-%s-first" % (PERSISTENCE_CELL, run_id)
        post(port, target_path, payload, cache_key, runs_dir / (stem + "-client-request.http"), runs_dir / (stem + "-client-response.http"))
        time.sleep(args.write_wait)
        families = []
        for repeat in range(1, PERSISTENCE_REPEATS + 1):
            target_path, payload = request_payload("responses", PERSISTENCE_CELL, run_id)
            stem = "%s-%s-repeat%d" % (PERSISTENCE_CELL, run_id, repeat)
            status, headers, body = post(
                port,
                target_path,
                payload,
                cache_key,
                runs_dir / (stem + "-client-request.http"),
                runs_dir / (stem + "-client-response.http"),
            )
            families.append(classify("responses", status, headers, body)["family"])
            time.sleep(0.5)
        calls = [
            record
            for record in read_jsonl(upstream_dir / "upstream.jsonl")
            if record.get("cell") == PERSISTENCE_CELL and record.get("run") == run_id
        ]
        runs.append({"repeat_families": families, "run": run_id, "upstream_calls": len(calls)})
    return {
        "repeats": PERSISTENCE_REPEATS,
        "runs": runs,
        "runs_all_repeats_wrong": sum(1 for item in runs if all(f != "response" for f in item["repeat_families"])),
        "upstream_calls_per_run": [item["upstream_calls"] for item in runs],
    }


def build_config(upstream_port, store, store_config, provider_key, config_db):
    return {
        "$schema": "https://www.getbifrost.ai/schema",
        "client": {
            "disable_content_logging": True,
            "enable_logging": False,
            "initial_pool_size": 10,
        },
        "config_store": {
            "config": {"path": str(config_db)},
            "enabled": True,
            "type": "sqlite",
        },
        "logs_store": {"enabled": False},
        "vector_store": {"config": store_config, "enabled": True, "type": store},
        "plugins": [
            {
                "config": {
                    "cache_by_model": True,
                    "cache_by_provider": True,
                    "dimension": 1,
                    "ttl": "5m",
                },
                "enabled": True,
                "name": "semantic_cache",
            }
        ],
        "providers": {
            "openai": {
                "keys": [
                    {
                        "models": ["*"],
                        "name": "kairo-synthetic",
                        "value": provider_key,
                        "weight": 1,
                    }
                ],
                "network_config": {"base_url": "http://127.0.0.1:%d" % upstream_port},
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
    for file_path in output_dir.rglob("*"):
        if file_path.is_file() and RUNTIME_PROVIDER_KEY.encode() in file_path.read_bytes():
            raise AssertionError("unsanitized runtime key found in %s" % file_path)


def run(args):
    source = Path(args.source).resolve()
    binary = Path(args.binary).resolve()
    commit = command_output(["git", "rev-parse", "HEAD"], source)
    if commit not in PINS:
        raise AssertionError("unpinned Bifrost commit %s" % commit)
    pin = PINS[commit]
    subprocess.check_call(
        ["git", "diff", "--exit-code", "HEAD", "--", "plugins/semanticcache", "core", "framework"],
        cwd=source,
        stdout=subprocess.DEVNULL,
    )
    plugin_version = (source / "plugins" / "semanticcache" / "version").read_text().strip()
    core_version = (source / "core" / "version").read_text().strip()

    output_dir = Path(args.output).resolve() if args.output else Path(tempfile.mkdtemp(prefix="kairo-086-review-"))
    runs_dir = output_dir / "runs"
    upstream_dir = output_dir / "upstream"
    for directory in (runs_dir, upstream_dir):
        directory.mkdir(parents=True, exist_ok=True)

    runtime_dir = Path(tempfile.mkdtemp(prefix="kairo-086-runtime-"))
    upstream_port = find_port()
    gateway_port = find_port()
    processes, logs = [], []
    try:
        store_config = {}
        qdrant_version = None
        if args.store == "qdrant":
            qdrant = Path(args.qdrant_bin).resolve()
            qdrant_version = command_output([str(qdrant), "--version"], runtime_dir)
            qdrant_http, qdrant_grpc = find_port(), find_port()
            qdrant_log = (output_dir / "qdrant.log").open("w")
            logs.append(qdrant_log)
            qdrant_process = subprocess.Popen(
                [str(qdrant)],
                cwd=runtime_dir,
                env=safe_child_env(
                    {
                        "QDRANT__LOG_LEVEL": "WARN",
                        "QDRANT__SERVICE__GRPC_PORT": str(qdrant_grpc),
                        "QDRANT__SERVICE__HTTP_PORT": str(qdrant_http),
                        "QDRANT__STORAGE__SNAPSHOTS_PATH": str(runtime_dir / "qdrant-snapshots"),
                        "QDRANT__STORAGE__STORAGE_PATH": str(runtime_dir / "qdrant-storage"),
                        "QDRANT__TELEMETRY_DISABLED": "true",
                    },
                    runtime_dir,
                ),
                stdout=qdrant_log,
                stderr=subprocess.STDOUT,
            )
            processes.append(qdrant_process)
            wait_http_ok(qdrant_http, "/readyz", qdrant_process)
            store_config = {"host": "127.0.0.1", "port": str(qdrant_grpc)}

        config = build_config(upstream_port, args.store, store_config, RUNTIME_PROVIDER_KEY, runtime_dir / "config.db")
        (runtime_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")
        sanitized_store = dict(store_config)
        if "port" in sanitized_store:
            sanitized_store["port"] = "<QDRANT_GRPC_PORT>"
        sanitized = build_config(
            upstream_port, args.store, sanitized_store, "<SYNTHETIC_PROVIDER_KEY>", "<RUNTIME_DIR>/config.db"
        )
        sanitized["providers"]["openai"]["network_config"]["base_url"] = "http://127.0.0.1:<UPSTREAM_PORT>"
        (output_dir / "config.sanitized.json").write_text(json.dumps(sanitized, indent=2) + "\n")

        target = {
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "commit": commit,
            "core_version_file": core_version,
            "label": pin["label"],
            "model": MODEL,
            "public_endpoints": ["POST /v1/chat/completions", "POST /v1/responses"],
            "semanticcache_version_file": plugin_version,
            "source": "https://github.com/maximhq/bifrost",
            "vector_store": args.store,
        }
        if qdrant_version:
            target["qdrant_version"] = qdrant_version

        script_dir = Path(__file__).resolve().parent
        upstream_log = (output_dir / "upstream-server.log").open("w")
        gateway_log = (output_dir / "bifrost.log").open("w")
        logs.extend([upstream_log, gateway_log])
        upstream_process = subprocess.Popen(
            [sys.executable, str(script_dir / "mock_upstream.py")],
            env=safe_child_env(
                {"CAPTURE_DIR": str(upstream_dir), "PORT": str(upstream_port), "PYTHONUNBUFFERED": "1"},
                runtime_dir,
            ),
            stdout=upstream_log,
            stderr=subprocess.STDOUT,
        )
        processes.append(upstream_process)
        wait_port(upstream_port, upstream_process)
        gateway_process = subprocess.Popen(
            [
                str(binary),
                "-app-dir", str(runtime_dir),
                "-host", "127.0.0.1",
                "-log-level", "info",
                "-log-style", "json",
                "-port", str(gateway_port),
            ],
            cwd=runtime_dir,
            env=safe_child_env({}, runtime_dir),
            stdout=gateway_log,
            stderr=subprocess.STDOUT,
        )
        processes.append(gateway_process)
        runtime_version = json.loads(wait_http_ok(gateway_port, "/api/version", gateway_process))
        if runtime_version != pin["runtime_version"]:
            raise AssertionError("expected runtime %s, got %r" % (pin["runtime_version"], runtime_version))
        target["runtime_version"] = runtime_version
        (output_dir / "target.json").write_text(json.dumps(target, indent=2, sort_keys=True) + "\n")

        results = {"cells": {}, "complete": False, "runs": args.runs, "target": target}
        results["warmup"] = warm_up(gateway_port, upstream_dir, runs_dir)
        for cell, (first, second, send_key) in CELLS.items():
            cell_runs = []
            for iteration in range(1, args.runs + 1):
                run_id = "%02d" % iteration
                cache_key = "kairo-086-%s-%s" % (cell, run_id) if send_key else None
                observed = {}
                for phase, kind in (("first", first), ("second", second)):
                    target_path, payload = request_payload(kind, cell, run_id)
                    stem = "%s-%s-%s" % (cell, run_id, phase)
                    status, headers, body = post(
                        gateway_port,
                        target_path,
                        payload,
                        cache_key,
                        runs_dir / (stem + "-client-request.http"),
                        runs_dir / (stem + "-client-response.http"),
                    )
                    observed[phase] = dict(classify(kind, status, headers, body), kind=kind)
                    if phase == "first":
                        time.sleep(args.write_wait)
                upstream_calls = [
                    record
                    for record in read_jsonl(upstream_dir / "upstream.jsonl")
                    if record.get("cell") == cell and record.get("run") == run_id
                ]
                expected = EXPECTED_FAMILY[second]
                wrong_family = observed["second"]["family"] != expected
                entry = {
                    "first": observed["first"],
                    "run": run_id,
                    "second": observed["second"],
                    "second_expected_family": expected,
                    "second_wrong_family": wrong_family,
                    "upstream_calls": len(upstream_calls),
                    "upstream_targets": [record["target"] for record in upstream_calls],
                }
                cell_runs.append(entry)
                if iteration == 1:
                    for phase in ("first", "second"):
                        for side in ("request", "response"):
                            name = "%s-%s-client-%s.http" % (cell, phase, side)
                            shutil.copyfile(runs_dir / ("%s-%s-%s-client-%s.http" % (cell, run_id, phase, side)), output_dir / name)
            results["cells"][cell] = {
                "runs": cell_runs,
                "second_wrong_family": sum(1 for item in cell_runs if item["second_wrong_family"]),
                "upstream_calls_per_run": [item["upstream_calls"] for item in cell_runs],
            }

        results["persistence"] = measure_persistence(gateway_port, upstream_dir, runs_dir, args)

        failures = []
        persistence = results["persistence"]
        if persistence["runs_all_repeats_wrong"] != args.runs or set(persistence["upstream_calls_per_run"]) != {1}:
            failures.append("persistence cell did not stay poisoned %d/%d" % (args.runs, args.runs))
        if args.consumer_python:
            subprocess.check_call(
                [
                    args.consumer_python,
                    str(script_dir / "consumer.py"),
                    "--gateway", "http://127.0.0.1:%d/v1" % gateway_port,
                    "--output", str(output_dir),
                    "--runs", str(args.runs),
                ],
                env=safe_child_env({"PYTHONUNBUFFERED": "1"}, runtime_dir),
            )
            consumer = json.loads((output_dir / "consumer-results.json").read_text())
            upstream = read_jsonl(upstream_dir / "upstream.jsonl")
            summary = {"sdk": consumer["sdk"], "cells": {}}
            for cell, value in consumer["cells"].items():
                calls = [
                    len([r for r in upstream if r.get("cell") == cell and r.get("run") == item["run"]])
                    for item in value["runs"]
                ]
                summary["cells"][cell] = {
                    "final_healthy": value["final_healthy"],
                    "final_outcomes": [
                        item["steps"][item["final_step"]].get("error") or "ok" for item in value["runs"]
                    ],
                    "upstream_calls_per_run": calls,
                }
                is_control = "_control_" in cell
                expected_calls = {"consumer_control_agents_alone": 1}.get(cell, 2 if is_control else 1)
                expected_healthy = args.runs if is_control else 0
                if value["final_healthy"] != expected_healthy or set(calls) != {expected_calls}:
                    failures.append("consumer cell %s did not match its expectation" % cell)
            results["consumer"] = summary
        for cell in VIOLATION_CELLS:
            summary = results["cells"][cell]
            if summary["second_wrong_family"] != args.runs or set(summary["upstream_calls_per_run"]) != {1}:
                failures.append("%s did not reproduce %d/%d" % (cell, args.runs, args.runs))
        same_family = ("control_chat_then_chat", "control_responses_then_responses")
        for cell in same_family:
            summary = results["cells"][cell]
            if summary["second_wrong_family"] or set(summary["upstream_calls_per_run"]) != {1}:
                failures.append("%s was not a clean cache hit" % cell)
        for cell in ("control_no_cache_key", "control_typed_responses_item"):
            summary = results["cells"][cell]
            if summary["second_wrong_family"] or set(summary["upstream_calls_per_run"]) != {2}:
                failures.append("%s was not a clean miss" % cell)
        results["failures"] = failures
        results["complete"] = not failures
        (output_dir / "results.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
        scan_sanitized(output_dir)
        summary = {
            cell: {
                "second_wrong_family": value["second_wrong_family"],
                "upstream_calls_per_run": value["upstream_calls_per_run"],
            }
            for cell, value in results["cells"].items()
        }
        print(json.dumps({"target": target, "summary": summary, "failures": failures}, indent=2, sort_keys=True))
        print("evidence: %s" % output_dir)
        return 0 if not failures else 1
    finally:
        for process in reversed(processes):
            stop_process(process)
        for log in logs:
            log.close()
        shutil.rmtree(runtime_dir, ignore_errors=True)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=os.environ.get("BIFROST_SOURCE"))
    parser.add_argument("--binary", default=os.environ.get("BIFROST_BIN"))
    parser.add_argument("--store", choices=["qdrant", "chromem"], default=os.environ.get("VECTOR_STORE", "qdrant"))
    parser.add_argument("--qdrant-bin", default=os.environ.get("QDRANT_BIN"))
    parser.add_argument("--output", default=os.environ.get("OUTPUT_DIR"))
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--write-wait", type=float, default=1.0)
    parser.add_argument(
        "--consumer-python",
        default=os.environ.get("CONSUMER_PYTHON"),
        help="Python with openai and openai-agents installed; runs consumer.py against the live gateway",
    )
    args = parser.parse_args()
    if not args.source or not args.binary:
        parser.error("set BIFROST_SOURCE and BIFROST_BIN or pass --source and --binary")
    if args.store == "qdrant" and not args.qdrant_bin:
        parser.error("--store qdrant needs QDRANT_BIN or --qdrant-bin")
    if args.runs < 1:
        parser.error("--runs must be positive")
    return args


if __name__ == "__main__":
    sys.exit(run(parse_args()))
