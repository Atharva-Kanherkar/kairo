#!/usr/bin/env python3
"""Reproduce kairo 087 through LiteLLM's public proxy entry point.

The deterministic upstream asks LiteLLM to execute one side-effecting MCP tool,
then fails the follow-up model call with HTTP 500. LiteLLM router retries replay
the complete MCP agent loop and execute the tool again. The controls disable
the fault or set ``num_retries: 0``.

The official OpenAI Python SDK sends one non-streaming Chat Completions request
through a loopback capture relay to the real ``litellm`` proxy executable. Raw
client HTTP, upstream HTTP, and the MCP ledger are saved for every trial.
"""

import argparse
import contextlib
import hashlib
import http.client
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time

RUNS = 5
HOST = "127.0.0.1"
MASTER_KEY = "sk-kairo-" + "087-master"
UPSTREAM_KEY = "sk-kairo-" + "087-upstream-only"
CLIENT_KEY = "sk-kairo-" + "087-client"
CREDENTIAL_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}")
ABSOLUTE_LOCAL_PATH = re.compile(r"/(?:Users|home|private|tmp|var/folders)/[^\s\"']+")

CELLS = {
    "violation_default_retries": {
        "fault": "on", "router_retries": None, "sdk_retries": 0,
        "expected_tools": 3, "expected_upstream": 6,
    },
    "violation_num_retries_2": {
        "fault": "on", "router_retries": 2, "sdk_retries": 0,
        "expected_tools": 3, "expected_upstream": 6,
    },
    "control_fault_off": {
        "fault": "off", "router_retries": 2, "sdk_retries": 0,
        "expected_tools": 1, "expected_upstream": 2,
    },
    "control_num_retries_0": {
        "fault": "on", "router_retries": 0, "sdk_retries": 0,
        "expected_tools": 1, "expected_upstream": 2,
    },
}


class ReproductionError(Exception):
    """The real run did not satisfy the evidence contract."""


def require(condition, message):
    if not condition:
        raise ReproductionError(message)


def sanitize_text(value):
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
    for secret, marker in (
        (MASTER_KEY, "<SYNTHETIC_MASTER_KEY>"),
        (UPSTREAM_KEY, "<SYNTHETIC_UPSTREAM_KEY>"),
        (CLIENT_KEY, "<SYNTHETIC_CLIENT_KEY>"),
    ):
        text = text.replace(secret, marker)
    require(not CREDENTIAL_PATTERN.search(text), "possible credential in captured bytes")
    return text


def sanitize_headers(headers):
    sanitized = []
    for name, value in headers:
        if name.lower() == "authorization":
            value = "Bearer <SYNTHETIC_CLIENT_KEY>"
        sanitized.append((name, sanitize_text(value)))
    return sanitized


def format_http(start_line, headers, body):
    return start_line + "\r\n" + "\r\n".join(
        "%s: %s" % pair for pair in headers
    ) + "\r\n\r\n" + sanitize_text(body)


def free_port():
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def wait_port(port, process, timeout=45):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise ReproductionError("child exited before opening port %d" % port)
        try:
            with socket.create_connection((HOST, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise ReproductionError("timed out waiting for port %d" % port)


def wait_health(port, process, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise ReproductionError("LiteLLM exited before becoming healthy")
        connection = None
        try:
            connection = http.client.HTTPConnection(HOST, port, timeout=1)
            connection.request("GET", "/health/liveliness")
            response = connection.getresponse()
            response.read()
            if response.status == 200:
                return
        except OSError:
            pass
        finally:
            if connection is not None:
                connection.close()
        time.sleep(0.3)
    raise ReproductionError("LiteLLM health check timed out")


class CaptureServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, proxy_port):
        super().__init__(address, handler)
        self.proxy_port = proxy_port
        self._records = []
        self._lock = threading.Lock()

    def append(self, record):
        with self._lock:
            self._records.append(record)

    def snapshot(self):
        with self._lock:
            return list(self._records)


class RelayHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_POST(self):
        request_body = self.rfile.read(int(self.headers.get("content-length", "0")))
        connection = http.client.HTTPConnection(HOST, self.server.proxy_port, timeout=300)
        connection.request(
            "POST", self.path, body=request_body,
            headers={
                "content-type": self.headers.get("content-type", "application/json"),
                "authorization": "Bearer " + MASTER_KEY,
            },
        )
        response = connection.getresponse()
        response_body = response.read()
        response_headers = response.getheaders()
        connection.close()

        request_headers = sanitize_headers(self.headers.items())
        safe_response_headers = sanitize_headers(response_headers)
        self.server.append({
            "request": format_http(
                "%s %s HTTP/1.1" % (self.command, self.path), request_headers, request_body
            ),
            "response": format_http(
                "HTTP/1.1 %d %s" % (response.status, response.reason),
                safe_response_headers, response_body,
            ),
            "status": response.status,
        })

        self.send_response(response.status)
        content_type = dict((name.lower(), value) for name, value in response_headers).get(
            "content-type", "application/json"
        )
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)


@contextlib.contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def child_env(extra, runtime):
    env = {
        "HOME": str(runtime),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(runtime),
        "PYTHONUNBUFFERED": "1",
        "LITELLM_LOG": "ERROR",
        "STORE_MODEL_IN_DB": "False",
        "KAIRO_087_MASTER_KEY": MASTER_KEY,
        "KAIRO_087_UPSTREAM_KEY": UPSTREAM_KEY,
    }
    env.update(extra)
    return env


def write_config(path, upstream_port, mcp_port, router_retries):
    lines = [
        "model_list:", "- model_name: kairo-model", "  litellm_params:",
        "    model: openai/kairo-087-model",
        "    api_base: http://127.0.0.1:%d/v1" % upstream_port,
        "    api_key: os.environ/KAIRO_087_UPSTREAM_KEY",
        "mcp_servers:", "  kairoledger:",
        "    url: http://127.0.0.1:%d/mcp" % mcp_port,
        "    transport: http", "general_settings:",
        "  master_key: os.environ/KAIRO_087_MASTER_KEY",
        "litellm_settings:", "  telemetry: false",
    ]
    if router_retries is not None:
        lines.append("  num_retries: %d" % router_retries)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


CLIENT = r"""
import json
import sys
from openai import OpenAI

client = OpenAI(base_url=sys.argv[1], api_key="sk-kairo-" + "087-client", max_retries=int(sys.argv[2]))
try:
    response = client.chat.completions.create(
        model="kairo-model",
        messages=[{"role": "user", "content": sys.argv[3]}],
        tools=[{"type": "mcp", "server_label": "kairoledger", "server_url": "litellm_proxy", "require_approval": "never"}],
        extra_body={"tool_choice": "required"},
    )
    print(json.dumps({"ok": True, "content": response.choices[0].message.content, "error": None}, separators=(",", ":")))
except Exception as exc:
    print(json.dumps({"ok": False, "content": None, "error": type(exc).__name__}, separators=(",", ":")))
"""


def send_request(python, relay_port, sdk_retries, prompt):
    completed = subprocess.run(
        [python, "-c", CLIENT, "http://%s:%d/v1" % (HOST, relay_port), str(sdk_retries), prompt],
        capture_output=True, text=True, timeout=360, check=False,
    )
    require(completed.stdout.strip(), "SDK client produced no JSON result")
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError as error:
        raise ReproductionError("SDK client output was not JSON") from error
    require(completed.returncode == 0, "SDK client process failed")
    return result


def package_metadata(python):
    package_script = (
        "import importlib.metadata,json;"
        "print(json.dumps({n:importlib.metadata.version(n) for n in ['litellm','openai','mcp']}))"
    )
    packages = json.loads(subprocess.check_output([python, "-c", package_script], text=True))
    source_script = "from pathlib import Path;import litellm;print(Path(litellm.__file__).resolve())"
    source = Path(subprocess.check_output([python, "-c", source_script], text=True).strip())
    return {
        "litellm_version": packages["litellm"],
        "openai_version": packages["openai"],
        "mcp_version": packages["mcp"],
        "python": subprocess.check_output([python, "--version"], text=True).strip().split()[-1],
        "litellm_init_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }


def stop_process(process):
    if process is None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def snapshot_files(directory):
    return {path.name for path in directory.glob("upstream-*") if path.is_file()}


def ledger_records(path):
    if not path.exists():
        return []
    records = []
    previous_sequence = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ReproductionError("malformed ledger line %d" % number) from error
        sequence = record.get("seq")
        require(isinstance(sequence, int) and sequence > 0, "ledger sequence is invalid")
        if previous_sequence is not None:
            require(sequence == previous_sequence + 1, "ledger sequence is inconsistent")
        require(record.get("entry") == "alpha", "ledger entry changed")
        records.append(record)
        previous_sequence = sequence
    return records


def copy_upstream_artifacts(source, names, destination):
    destination.mkdir(parents=True, exist_ok=True)
    for name in sorted(names):
        text = sanitize_text((source / name).read_bytes())
        require(not ABSOLUTE_LOCAL_PATH.search(text), "%s contains a local path" % name)
        (destination / name).write_text(text, encoding="utf-8")


def validate_run(name, spec, run, run_dir):
    label = "%s run %s" % (name, run.get("run"))
    require(run.get("tool_executions") == spec["expected_tools"], label + ": wrong tool count")
    require(run.get("upstream_calls") == spec["expected_upstream"], label + ": wrong upstream count")
    require((run_dir / "client-request.http").is_file(), label + ": missing client request")
    require((run_dir / "client-response.http").is_file(), label + ": missing client response")
    ledger = ledger_records(run_dir / "tool-ledger.jsonl")
    require(len(ledger) == spec["expected_tools"], label + ": ledger disagrees with summary")
    metadata = list((run_dir / "upstream").glob("upstream-*.json"))
    require(len(metadata) == spec["expected_upstream"], label + ": raw upstream count disagrees")
    for meta in metadata:
        stem = meta.with_suffix("")
        require(stem.with_name(stem.name + "-request.http").is_file(), label + ": missing upstream request")
        require(stem.with_name(stem.name + "-response.http").is_file(), label + ": missing upstream response")
    if name == "control_fault_off":
        require(run.get("client_ok") is True, label + ": healthy control failed")
    else:
        require(run.get("client_ok") is False, label + ": faulting case unexpectedly succeeded")


def validate_matrix(summary, root):
    require(summary.get("runs") == RUNS, "matrix must contain five trials per cell")
    require(set(summary.get("cells", {})) == set(CELLS), "matrix cells are incomplete")
    for name, spec in CELLS.items():
        cell = summary["cells"][name]
        expected_spec = {
            "fault": spec["fault"], "router_retries": spec["router_retries"],
            "sdk_retries": spec["sdk_retries"],
        }
        require(cell.get("spec") == expected_spec, name + ": recorded spec differs from executed spec")
        runs = cell.get("runs")
        require(isinstance(runs, list) and len(runs) == RUNS, name + ": incomplete trials")
        for index, run in enumerate(runs, 1):
            require(run.get("run") == "%02d" % index, name + ": trial order is inconsistent")
            validate_run(name, spec, run, root / "cells" / name / "runs" / ("run-%02d" % index))
        require(cell.get("tool_executions_per_run") == [spec["expected_tools"]] * RUNS,
                name + ": tool summary is inconsistent")
        require(cell.get("upstream_calls_per_run") == [spec["expected_upstream"]] * RUNS,
                name + ": upstream summary is inconsistent")


def run_cell(args, name, spec, output_dir):
    runtime = Path(tempfile.mkdtemp(prefix="kairo-087-%s-" % name))
    cell_dir = output_dir / "cells" / name
    runs_dir = cell_dir / "runs"
    capture_dir = runtime / "upstream"
    ledger = runtime / "ledger.jsonl"
    config = runtime / "config.yaml"
    logs = runtime / "logs"
    for directory in (runs_dir, capture_dir, logs):
        directory.mkdir(parents=True, exist_ok=True)

    ports = []
    while len(set(ports)) < 4:
        ports = [free_port() for _ in range(4)]
    upstream_port, mcp_port, proxy_port, relay_port = ports
    write_config(config, upstream_port, mcp_port, spec["router_retries"])
    (cell_dir / "config.yaml").write_text(config.read_text(encoding="utf-8"), encoding="utf-8")

    python = os.path.abspath(args.python)
    script_dir = Path(__file__).resolve().parent
    upstream = mcp = proxy = None
    log_handles = []
    results = []
    try:
        def start(command, log_name, extra):
            handle = (logs / log_name).open("w", encoding="utf-8")
            log_handles.append(handle)
            return subprocess.Popen(
                command, stdout=handle, stderr=subprocess.STDOUT, text=True,
                env=child_env(extra, runtime),
            )

        upstream = start(
            [python, str(script_dir / "mock_upstream.py")], "upstream.log",
            {"UPSTREAM_PORT": str(upstream_port), "CAPTURE_DIR": str(capture_dir), "FAULT": spec["fault"]},
        )
        mcp = start(
            [python, str(script_dir / "mcp_server.py")], "mcp.log",
            {"MCP_PORT": str(mcp_port), "LEDGER_PATH": str(ledger)},
        )
        wait_port(upstream_port, upstream)
        wait_port(mcp_port, mcp)
        litellm_bin = str(Path(python).with_name("litellm"))
        require(Path(litellm_bin).is_file(), "Python environment has no litellm executable")
        proxy = start(
            [litellm_bin, "--config", str(config), "--port", str(proxy_port)],
            "proxy.log", {},
        )
        wait_health(proxy_port, proxy)

        relay = CaptureServer((HOST, relay_port), RelayHandler, proxy_port)
        with serving(relay):
            for index in range(1, RUNS + 1):
                before_upstream = snapshot_files(capture_dir)
                before_ledger = len(ledger_records(ledger))
                before_client = len(relay.snapshot())
                consumer = send_request(
                    python, relay_port, spec["sdk_retries"], "write alpha run %02d" % index
                )
                after_client = relay.snapshot()
                require(len(after_client) == before_client + 1, "SDK made more than one public request")
                exchange = after_client[-1]
                new_upstream = snapshot_files(capture_dir) - before_upstream
                new_metadata = [filename for filename in new_upstream if filename.endswith(".json")]
                new_ledger = ledger_records(ledger)[before_ledger:]

                run_dir = runs_dir / ("run-%02d" % index)
                run_dir.mkdir(parents=True)
                copy_upstream_artifacts(capture_dir, new_upstream, run_dir / "upstream")
                (run_dir / "client-request.http").write_text(exchange["request"], encoding="utf-8")
                (run_dir / "client-response.http").write_text(exchange["response"], encoding="utf-8")
                (run_dir / "tool-ledger.jsonl").write_text(
                    "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in new_ledger),
                    encoding="utf-8",
                )
                result = {
                    "run": "%02d" % index,
                    "client_ok": consumer.get("ok"),
                    "client_error": consumer.get("error"),
                    "client_status": exchange["status"],
                    "tool_executions": len(new_ledger),
                    "upstream_calls": len(new_metadata),
                }
                (run_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
                validate_run(name, spec, result, run_dir)
                results.append(result)
    finally:
        for process in (proxy, mcp, upstream):
            stop_process(process)
        for handle in log_handles:
            handle.close()
        shutil.rmtree(runtime, ignore_errors=True)

    return {
        "spec": {
            "fault": spec["fault"], "router_retries": spec["router_retries"],
            "sdk_retries": spec["sdk_retries"],
        },
        "runs": results,
        "tool_executions_per_run": [result["tool_executions"] for result in results],
        "upstream_calls_per_run": [result["upstream_calls"] for result in results],
    }


def reproduce(args):
    output = Path(args.output_dir).resolve()
    require(not output.exists(), "output directory already exists")
    python = os.path.abspath(args.python)
    require(Path(python).is_file(), "--python does not name an executable")
    metadata = package_metadata(python)
    require(metadata["litellm_version"] == args.expect_litellm,
            "installed LiteLLM version differs from --expect-litellm")
    output.mkdir(parents=True)
    summary = {
        "target": {
            **metadata, "project": "BerriAI/litellm", "source_ref": args.source_ref,
            "captured_at": args.captured_at, "endpoint": "POST /v1/chat/completions",
            "tool": "MCP gateway auto-execution, require_approval=never, tool_choice=required",
        },
        "runs": RUNS, "cells": {}, "complete": False, "failures": [],
    }
    for name, spec in CELLS.items():
        print("running %s" % name, flush=True)
        summary["cells"][name] = run_cell(args, name, spec, output)
    validate_matrix(summary, output)
    summary["complete"] = True
    (output / "results.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("LiteLLM %s: all four cells matched 5/5" % args.expect_litellm)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, help="Python executable in the target environment")
    parser.add_argument("--expect-litellm", required=True, help="exact installed LiteLLM version")
    parser.add_argument("--source-ref", required=True, help="release tag or commit tested")
    parser.add_argument("--captured-at", required=True, help="UTC capture date")
    parser.add_argument("--output-dir", required=True, help="fresh output directory")
    args = parser.parse_args()
    try:
        reproduce(args)
    except (OSError, ReproductionError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        print("reproduction failed: %s" % error, file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
