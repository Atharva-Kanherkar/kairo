#!/usr/bin/env python3
"""Reproduce kairo issue 093: a Bifrost MCP gateway POST /mcp tools/call
response drops the upstream isError flag, so a tool the upstream MCP server
reported as FAILED reaches the MCP client as a SUCCESS.

Nothing is built by this script except the two rig binaries it needs. It starts
a real Bifrost HTTP gateway from a pinned checkout, connects it to a real stdio
MCP server over a real byte-logging stdio relay, and drives the public
`POST /mcp` route over a real byte-logging TCP relay so that every leg is
evidenced from raw bytes.

Four cells, five runs each:

  violation_gateway_iserror  gateway, failing tool      expect isError ABSENT (violation)
  control_direct_stdio       no gateway, same tool     expect isError PRESENT and true
  control_gateway_success    gateway, succeeding tool  expect isError ABSENT, content present
  differential_pr7640        gateway at PR #7640 head  expect isError PRESENT and true

The first and second cells differ in exactly one thing: whether the gateway is
in the path. The first and third differ in exactly one thing: which of the two
tools the MCP server answers, and those two handlers differ only in the result
constructor they call.

No provider credential is needed. No child process inherits the caller's
environment beyond PATH, HOME and TMPDIR. By default evidence goes to a fresh
ignored temporary directory; pass --output transcripts/093 to freeze an author
run in place. Passing --output also runs scan_sanitized over what was written.
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
import threading
import time
from pathlib import Path

# Pinned targets. The tag is the current release. The PR head is the upstream fix
# under differential test. The base commit is the PR head's parent, used to prove
# the PR head is not differing from the tag for any other reason.
PINNED_TAG = "ed8371a9779bfbc8aa689d4d77964cf8ce9308bf"
PINNED_TAG_LABEL = "transports/v2.2.4"
PINNED_TAG_CORE_VERSION = "1.11.0"
PR_HEAD = "862b3bea5cd8a940d52fefdfb57a0741efc48c84"
PR_BASE = "f8fccb93c9817668670323b5a7ef57458050602d"
PR_HEAD_CORE_VERSION = "1.10.4"

FAIL_TOOL = "kairofail-always_fails"
OK_TOOL = "kairofail-always_ok"
# The gateway namespaces upstream tools as "<client name>-<upstream tool name>".
GATEWAY_FAIL_TOOL = "KairoFail-" + FAIL_TOOL
GATEWAY_OK_TOOL = "KairoFail-" + OK_TOOL

CELLS = {
    # name: (gateway?, upstream tool, gateway tool name, expect isError present)
    "violation_gateway_iserror": (True, FAIL_TOOL, GATEWAY_FAIL_TOOL, False),
    "control_direct_stdio": (False, FAIL_TOOL, None, True),
    "control_gateway_success": (True, OK_TOOL, GATEWAY_OK_TOOL, False),
    "differential_pr7640": (True, FAIL_TOOL, GATEWAY_FAIL_TOOL, True),
}


def build_bifrost(source, output_binary, ui_stub=True):
    """Build the transports binary from a pinned checkout.

    The published image is not available here, so the pinned tag is compiled
    instead. CGO is required for the sqlite config store. The embedded UI is a
    compile-time `all:ui` pattern and is not on the /mcp request path, so a
    placeholder satisfies it; the placeholder is reported in target.json rather
    than being passed over silently.

    The build refuses to proceed if the tree has modifications under any path
    that could affect the request path, so a fixed binary cannot be presented as
    the pinned one.
    """
    source = Path(source)
    ui_dir = source / "transports" / "bifrost-http" / "ui"
    if ui_stub:
        ui_dir.mkdir(parents=True, exist_ok=True)
        (ui_dir / "index.html").write_text(
            "<!doctype html><title>kairo ui embed placeholder</title>\n"
        )
    diff = command_output(
        ["git", "status", "--porcelain", "--", "core", "transports/bifrost-http/handlers",
         "transports/bifrost-http/server", "transports/bifrost-http/lib"],
        source,
    )
    handler_diff = [
        line for line in diff.splitlines()
        if line.strip().endswith(".go") or ".go" in line
    ]
    if handler_diff:
        raise AssertionError(
            "source under test has Go modifications, refusing to attribute bytes to it:\n"
            + "\n".join(handler_diff)
        )
    subprocess.check_call(
        ["go", "build", "-o", str(output_binary), "./bifrost-http"],
        cwd=str(source / "transports"),
    )
    return output_binary


def build_rig(rig_dir, output_dir):
    """Compile the four rig binaries with the pinned SDK version."""
    rig = Path(rig_dir).resolve()
    built = {}
    for name, package in (
        ("kairoserver", "./server"),
        ("kairotee", "./tee"),
        ("kairodirect", "./direct"),
        ("kairoconsumer", "./consumer"),
        ("kairosdkprobe", "./sdkprobe"),
    ):
        target = Path(output_dir) / name
        subprocess.check_call(["go", "build", "-o", str(target), package], cwd=str(rig))
        built[name] = target
    return built


def compact(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def command_output(command, cwd):
    return subprocess.check_output(command, cwd=cwd, text=True).strip()


def find_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_port(port, process, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("child exited before opening port %d" % port)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("timed out waiting for port %d" % port)


def wait_gateway(port, process, timeout=90):
    deadline = time.time() + timeout
    last = None
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
                return body
        except Exception as error:  # noqa: BLE001 - readiness polling
            last = error
        time.sleep(0.2)
    raise RuntimeError("gateway readiness timed out: %r" % (last,))


def stop_process(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def safe_child_env(extra, temp_dir):
    """A deliberately minimal environment. Nothing secret is forwarded."""
    child = {
        "HOME": str(temp_dir),
        "LANG": "C.UTF-8",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "TMPDIR": str(temp_dir),
    }
    child.update(extra)
    return child


class RawRelay:
    """A transparent TCP relay that records every byte in both directions.

    This exists so the HTTP leg is evidenced from socket bytes rather than from
    an http.client reconstruction. It parses nothing, rewrites nothing, and
    forwards unchanged, so what the log shows is what crossed the socket.
    """

    def __init__(self, listen_port, target_port, log_path):
        self.listen_port = listen_port
        self.target_port = target_port
        self.log_path = log_path
        self.server = None
        self.thread = None

    def start(self):
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", self.listen_port))
        self.server.listen(16)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                client_socket, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(
                target=self._session, args=(client_socket,), daemon=True
            ).start()

    def _session(self, client_socket):
        try:
            upstream = socket.create_connection(("127.0.0.1", self.target_port), timeout=30)
        except OSError:
            client_socket.close()
            return
        with open(self.log_path, "ab") as log:
            lock = threading.Lock()

            def pump(source, sink, direction):
                try:
                    while True:
                        data = source.recv(65536)
                        if not data:
                            break
                        with lock:
                            log.write(b"<<<" + direction.encode() + b">>>" + data)
                            log.flush()
                        sink.sendall(data)
                except OSError:
                    pass
                finally:
                    try:
                        sink.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass

            forward = threading.Thread(
                target=pump, args=(client_socket, upstream, "client-to-gateway"), daemon=True
            )
            forward.start()
            pump(upstream, client_socket, "gateway-to-client")
            forward.join(timeout=5)
            client_socket.close()
            upstream.close()

    def stop(self):
        if self.server is not None:
            self.server.close()


def raw_http_post(port, target, body, request_path, response_path):
    """One POST over a plain socket, recording the exact bytes both ways."""
    body_bytes = body.encode()
    request = (
        "POST %s HTTP/1.1\r\n"
        "Host: 127.0.0.1:%d\r\n"
        "Content-Type: application/json\r\n"
        "Accept: application/json, text/event-stream\r\n"
        "Content-Length: %d\r\n"
        "Connection: close\r\n"
        "\r\n" % (target, port, len(body_bytes))
    ).encode() + body_bytes

    sock = socket.create_connection(("127.0.0.1", port), timeout=30)
    try:
        sock.sendall(request)
        chunks = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
    finally:
        sock.close()
    raw_response = b"".join(chunks)

    request_path.write_bytes(
        b"<<<client-to-gateway>>>" + request + b"<<<gateway-to-client>>>" + raw_response
    )
    response_path.write_bytes(raw_response)

    head, _, response_body = raw_response.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode("utf-8", "replace")
    status = int(status_line.split(" ")[1])
    return status, response_body.decode("utf-8", "replace")


def tool_result_object(response_body):
    """The JSON-RPC result object, or None when the body is not a tools/call
    success. A protocol-level error is reported as such rather than silently
    treated as a missing key."""
    try:
        value = json.loads(response_body)
    except ValueError:
        return None, "response body is not JSON"
    if not isinstance(value, dict):
        return None, "response body is not a JSON object"
    if value.get("error") is not None:
        return None, "JSON-RPC error: %s" % compact(value["error"])
    result = value.get("result")
    if not isinstance(result, dict):
        return None, "response has no result object"
    return result, None


def file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def read_from(path, offset):
    """Bytes appended to a file after `offset`, or "" when it does not exist."""
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            return handle.read()
    except OSError:
        return b""


def build_config(binary, gateway_runtime_dir, ledger_path, tee_log_path):
    """`binary` and `ledger_path` may be str or Path; the config is JSON."""
    """The documented anonymous MCP gateway configuration: one stdio MCP client,
    no auth, all tools executable, and inference auth not enforced. Nothing here
    is a workaround for the defect; it is the documented plain /mcp route."""
    return {
        "$schema": "https://www.getbifrost.ai/schema",
        "client": {
            "disable_content_logging": True,
            "enable_logging": False,
            "enforce_auth_on_inference": False,
            "initial_pool_size": 10,
        },
        "config_store": {
            "config": {"path": str(gateway_runtime_dir / "config.db")},
            "enabled": True,
            "type": "sqlite",
        },
        "logs_store": {"enabled": False},
        "mcp": {
            "client_configs": [
                {
                    "auth_type": "none",
                    "connection_type": "stdio",
                    "name": "KairoFail",
                    "stdio_config": {
                        # kairotee is the relay; kairoserver is the MCP server it
                        # proxies to. Both live beside the built Bifrost binary.
                        "args": [str(Path(binary).parent / "kairoserver")],
                        "command": str(Path(binary).parent / "kairotee"),
                        "envs": [
                            "KAIRO_LEDGER=%s" % str(ledger_path),
                            "KAIRO_TEE_LOG=%s" % str(tee_log_path),
                        ],
                    },
                    "tools_to_execute": ["*"],
                }
            ],
            "tool_manager_config": {
                "max_agent_depth": 5,
                "tool_execution_timeout": "5s",
            },
        },
    }


def start_gateway(binary, source, cell, runtime_dir, ledger_path, tee_log_path):
    port = find_port()
    config = build_config(binary, runtime_dir, ledger_path, tee_log_path)
    (runtime_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    log = (runtime_dir / "gateway.log").open("w")
    process = subprocess.Popen(
        [
            str(binary),
            "--app-dir", str(runtime_dir),
            "--port", str(port),
            "--host", "127.0.0.1",
            "--log-level", "info",
        ],
        cwd=str(source),
        stdout=log,
        stderr=subprocess.STDOUT,
        env=safe_child_env({}, runtime_dir),
    )
    process._kairo_log = log  # type: ignore[attr-defined]
    wait_port(port, process)
    version = wait_gateway(port, process)
    wait_for_tools(port, process)
    return process, port, version


def wait_for_tools(port, process, timeout=90):
    """Block until the gateway's MCP server serves both upstream tools.

    The gateway reports itself ready before its MCP tool sync has necessarily
    landed. Probing tools/list until the tools appear keeps readiness out of the
    measured path: the wait happens once per gateway, before any trial, and no
    trial is counted unless both tools were already served.
    """
    payload = compact({"jsonrpc": "2.0", "id": 0, "method": "tools/list", "params": {}})
    deadline = time.time() + timeout
    seen = set()
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Bifrost exited before serving its MCP tools")
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=5)
            try:
                sock.sendall(
                    (
                        "POST /mcp HTTP/1.1\r\n"
                        "Host: 127.0.0.1:%d\r\n"
                        "Content-Type: application/json\r\n"
                        "Accept: application/json, text/event-stream\r\n"
                        "Content-Length: %d\r\n"
                        "Connection: close\r\n\r\n%s"
                        % (port, len(payload.encode()), payload)
                    ).encode()
                )
                chunks = []
                while True:
                    data = sock.recv(65536)
                    if not data:
                        break
                    chunks.append(data)
            finally:
                sock.close()
            body = b"".join(chunks).partition(b"\r\n\r\n")[2].decode("utf-8", "replace")
            result, _ = tool_result_object(body)
            if result is not None:
                seen = {entry.get("name") for entry in result.get("tools", [])}
                if {GATEWAY_FAIL_TOOL, GATEWAY_OK_TOOL} <= seen:
                    return sorted(seen)
        except OSError:
            pass
        time.sleep(0.3)
    raise RuntimeError(
        "gateway never served both MCP tools; last tools/list returned %s" % sorted(seen)
    )


def run_direct(binary_dir, tool, run_dir, tee_log, ledger):
    """The gateway-absent leg. A real mcp-go stdio client, no Bifrost.

    The client is pointed at kairotee rather than straight at kairoserver so this
    leg's upstream bytes are recorded the same way the gateway leg's are. The
    relay is transparent, so the client still talks to the same server binary.
    """
    result = subprocess.run(
        [
            str(binary_dir / "kairodirect"),
            str(binary_dir / "kairotee"),
            str(binary_dir / "kairoserver"),
            tool,
            str(ledger),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=safe_child_env({"KAIRO_TEE_LOG": str(tee_log)}, run_dir),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("kairodirect failed: %s" % result.stderr.strip())
    (run_dir / "client-stdout.txt").write_text(result.stdout)
    # With no relay in the path, what the client received IS what the upstream
    # server emitted, so the upstream response frame is also saved as this run's
    # client response. The conformance suite reads one file name for every cell,
    # and here the two are the same bytes by construction rather than by
    # assertion.
    frame = latest_server_frame(Path(tee_log))
    if frame is None:
        raise RuntimeError("no upstream response frame recorded for the direct leg")
    (run_dir / "client-response.http").write_text(frame)
    for line in result.stdout.splitlines():
        if line.startswith("DIRECT "):
            return json.loads(line[len("DIRECT "):])
    raise RuntimeError("kairodirect produced no DIRECT record")


def latest_server_frame(tee_log):
    """The last upstream MCP response frame the relay recorded, verbatim."""
    try:
        data = tee_log.read_bytes()
    except OSError:
        return None
    frames = [
        line[len("<<<server-to-client>>>"):]
        for line in data.split(b"\n")
        if line.startswith(b"<<<server-to-client>>>")
    ]
    if not frames:
        return None
    return frames[-1].decode("utf-8", "replace")


def run_consumer(binary_dir, relay_port, tool, run_dir):
    """The consumer boundary: a real mcp-go streamable-HTTP MCP client."""
    result = subprocess.run(
        [
            str(binary_dir / "kairoconsumer"),
            "http://127.0.0.1:%d/mcp" % relay_port,
            tool,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=safe_child_env({}, run_dir),
        check=False,
    )
    (run_dir / "consumer-stdout.txt").write_text(result.stdout + result.stderr)
    if result.returncode != 0:
        raise RuntimeError(
            "kairoconsumer failed: %s" % (result.stderr.strip() or result.stdout.strip())
        )
    for line in result.stdout.splitlines():
        if line.startswith("CONSUMER "):
            return json.loads(line[len("CONSUMER "):])
    raise RuntimeError("kairoconsumer produced no CONSUMER record")


def summarize_cell(cell_name, runs):
    """N of N for one cell, derived only from recorded artifacts."""
    verdicts = [run["expectation_met"] for run in runs]
    return {
        "runs": len(runs),
        "is_error_key_present_per_run": [
            run["is_error_key_present_in_client_response"] for run in runs
        ],
        "expectation_met_per_run": verdicts,
        "expectation_met_n_of_n": "%d of %d" % (sum(verdicts), len(verdicts)),
        "client_classification_per_run": [run["client_classification"] for run in runs],
        "upstream_emitted_is_error_true_per_run": [
            run["raw_probe"]["upstream_emitted_is_error_true"] for run in runs
        ],
        "raw_probe_ledger_executions_per_run": [
            run["raw_probe"]["ledger_executions"] for run in runs
        ],
        "consumer_ledger_executions_per_run": [
            run["consumer_call"]["ledger_executions"] for run in runs
        ],
        "consumer_upstream_emitted_is_error_true_per_run": [
            run["consumer_call"]["upstream_emitted_is_error_true"] for run in runs
        ],
    }


def capture_slice(tee_log_path, ledger_path, tee_offset, ledger_offset, run_dir, stem):
    """Slice the upstream relay log and the tool ledger from the given offsets.

    Returns what the upstream MCP server actually put on the pipe for this call
    and how many tool executions it recorded, so the upstream half of the
    invariant is evidenced from bytes and the execution count is counted where
    the execution happens rather than inferred.
    """
    tee_bytes = read_from(tee_log_path, tee_offset)
    ledger_bytes = read_from(ledger_path, ledger_offset)
    (run_dir / ("upstream-stdio-%s.log" % stem)).write_bytes(tee_bytes)
    (run_dir / ("tool-ledger-%s.jsonl" % stem)).write_bytes(ledger_bytes)
    ledger_lines = [
        json.loads(line)
        for line in ledger_bytes.decode("utf-8", "replace").splitlines()
        if line.strip()
    ]
    return {
        "upstream_stdout_verbatim": tee_bytes.decode("utf-8", "replace"),
        "upstream_emitted_is_error_true": (
            b'"isError":true' in tee_bytes or b'"isError": true' in tee_bytes
        ),
        "ledger_executions": len(ledger_lines),
        "ledger": ledger_lines,
        "ledger_marks_tool_failed": any(
            entry.get("returned_flag") is True for entry in ledger_lines
        ),
    }


def run_cell(cell_name, spec, args, binaries, output_dir, source_root):
    through_gateway, upstream_tool, gateway_tool, expect_present = spec
    binary = binaries[cell_name]
    source = source_root[cell_name]
    cell_dir = output_dir / "cells" / cell_name
    runs_dir = cell_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir = Path(tempfile.mkdtemp(prefix="kairo-093-%s-" % cell_name))
    ledger_path = runtime_dir / "tool-ledger.jsonl"
    tee_log_path = runtime_dir / "tee.log"

    config_for_evidence = None
    gateway_process = None
    relay = None
    try:
        if through_gateway:
            gateway_process, gateway_port, version = start_gateway(
                binary, source, cell_name, runtime_dir, ledger_path, tee_log_path
            )
            # The frozen copy carries placeholders in place of every path that
            # is specific to the machine that ran it, so a re-run produces the
            # same bytes rather than a fresh scratch directory.
            config_for_evidence = build_config(
                "<BIN_DIR>/bifrost", Path("<RUNTIME_DIR>"), "<RUNTIME_DIR>", "<RUNTIME_DIR>"
            )
            (cell_dir / "gateway-version.json").write_text(version + "\n")
            relay_port = find_port()
            relay = RawRelay(relay_port, gateway_port, str(runs_dir / "raw-relay.log"))
            relay.start()

        runs = []
        for index in range(1, args.runs + 1):
            trial = "%02d" % index
            run_dir = runs_dir / ("run-" + trial)
            run_dir.mkdir(parents=True, exist_ok=True)
            record = {"run": trial}

            if through_gateway:
                relay_offset = file_size(runs_dir / "raw-relay.log")

                # Probe one: a single hand-written JSON-RPC POST over a plain
                # socket, so the client bytes and the gateway bytes are both
                # recorded verbatim.
                payload = compact({
                    "jsonrpc": "2.0",
                    "id": index,
                    "method": "tools/call",
                    "params": {"arguments": {}, "name": gateway_tool},
                })
                tee_before, ledger_before = file_size(tee_log_path), file_size(ledger_path)
                status, body = raw_http_post(
                    relay_port,
                    "/mcp",
                    payload,
                    run_dir / "client-request.http",
                    run_dir / "client-response.http",
                )
                record["client_status"] = status
                result, problem = tool_result_object(body)
                if problem is not None:
                    raise RuntimeError("%s run %s: %s" % (cell_name, trial, problem))
                record["raw_probe"] = capture_slice(
                    tee_log_path, ledger_path, tee_before, ledger_before,
                    run_dir, "raw-probe",
                )
                (run_dir / "relay-slice.log").write_bytes(
                    read_from(runs_dir / "raw-relay.log", relay_offset)
                )

                # Probe two: a real MCP client, separate call, separate upstream
                # slice, so the consumer classification and the raw POST cannot be
                # confused with one another.
                tee_before, ledger_before = file_size(tee_log_path), file_size(ledger_path)
                record["consumer"] = run_consumer(
                    binary_dir(binaries), relay_port, gateway_tool, run_dir
                )
                record["consumer_call"] = capture_slice(
                    tee_log_path, ledger_path, tee_before, ledger_before,
                    run_dir, "consumer",
                )
                consumer = record["consumer"]
            else:
                tee_before, ledger_before = file_size(tee_log_path), file_size(ledger_path)
                consumer = run_direct(
                    binary_dir(binaries), upstream_tool, run_dir, tee_log_path, ledger_path
                )
                record["consumer"] = consumer
                # The direct client reports the CallToolResult object itself,
                # which is the upstream tools/call `result` object verbatim, not
                # a JSON-RPC envelope. Presence of isError is read from it
                # directly for the same reason as on the HTTP leg.
                try:
                    result = json.loads(consumer["wire_result_verbatim"])
                except ValueError as error:
                    raise RuntimeError(
                        "%s run %s: direct result is not JSON: %s" % (cell_name, trial, error)
                    ) from error
                if not isinstance(result, dict):
                    raise RuntimeError("%s run %s: direct result is not an object" % (cell_name, trial))
                record["raw_probe"] = capture_slice(
                    tee_log_path, ledger_path, tee_before, ledger_before,
                    run_dir, "raw-probe",
                )
                # The direct leg makes exactly one call, and the raw probe is
                # that call, so there is no second slice to take.
                record["consumer_call"] = record["raw_probe"]
                record["client_status"] = None

            # Key PRESENCE, not value. The SDK omits isError when it is false, so
            # absent and false are the same wire fact, and an assertion on the
            # value alone would pass on a correct implementation.
            has_key = "isError" in result
            record["is_error_key_present_in_client_response"] = has_key
            record["client_classification"] = consumer["consumer_classifies"]
            record["client_content_texts"] = consumer.get("content_texts", [])
            record["client_result_object"] = result
            # The bare result object, so the SDK-level probe can decode exactly
            # the bytes the consumer received, whichever leg produced them.
            (run_dir / "client-result.json").write_text(
                json.dumps(result, separators=(",", ":"), sort_keys=True) + "\n"
            )
            record["client_result_verbatim"] = consumer["wire_result_verbatim"]
            record["expectation_met"] = has_key == expect_present

            (run_dir / "result.json").write_text(json.dumps(record, indent=2) + "\n")
            runs.append(record)

        summary = summarize_cell(cell_name, runs)
        summary["cell"] = cell_name
        summary["target"] = {
            "commit": command_output(["git", "rev-parse", "HEAD"], source),
            "core_version": (source / "core" / "version").read_text().strip(),
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        }
        summary["spec"] = {
            "through_gateway": through_gateway,
            "upstream_tool": upstream_tool,
            "gateway_tool": gateway_tool,
            "expect_is_error_key_present": expect_present,
        }
        if config_for_evidence is not None:
            (cell_dir / "config.sanitized.json").write_text(
                json.dumps(config_for_evidence, indent=2) + "\n"
            )
        return {"summary": summary, "runs": runs}
    finally:
        if relay is not None:
            relay.stop()
        stop_process(gateway_process)
        if gateway_process is not None and hasattr(gateway_process, "_kairo_log"):
            gateway_process._kairo_log.close()  # type: ignore[attr-defined]
        if os.environ.get("KAIRO_093_KEEP_RUNTIME"):
            print("  runtime kept at %s" % runtime_dir, flush=True)
        else:
            shutil.rmtree(runtime_dir, ignore_errors=True)


def run_sdk_probe(output_dir, binaries, args):
    """Positive SDK attribution, with no Bifrost in the process.

    The probe prints the bytes the pinned SDK emits for its own result
    constructors, then decodes each recorded client result through the same
    decoder a consumer uses. If the SDK dropped a flag it had been given, the
    constructor lines would show it.
    """
    probe = binary_dir(binaries) / "kairosdkprobe"
    if not probe.is_file():
        raise RuntimeError("missing SDK probe: %s" % probe)
    targets = []
    for cell_name in sorted(CELLS):
        for run_dir in sorted((output_dir / "cells" / cell_name / "runs").glob("run-*")):
            result_file = run_dir / "client-result.json"
            if result_file.is_file():
                targets.append(result_file)
    if not targets:
        raise RuntimeError("SDK probe found no recorded client results")
    output = subprocess.run(
        [str(probe)] + [str(path) for path in targets],
        capture_output=True,
        text=True,
        timeout=120,
        env=safe_child_env({}, output_dir),
        check=False,
    )
    header = [
        "# SDK-level attribution probe, generated by transcripts/093/reproduce.py.",
        "# No Bifrost process is involved in any line below.",
        "#",
        "# SDK lines: the exact bytes the pinned SDK (github.com/mark3labs/mcp-go",
        "# v0.43.2, the version the target vendors) emits for its own result",
        "# constructors, and what its own decoder reads back.",
        "# DECODE lines: each recorded client tools/call result decoded by that",
        "# same SDK, reported as key presence, decoded value, and what a consumer",
        "# branching on the flag would conclude.",
    ]
    body = [
        "# %s" % path.relative_to(output_dir) for path in targets
    ]
    (output_dir / "sdk-probe.txt").write_text(
        "\n".join(header + [""] + output.stdout.splitlines() + [""] + body) + "\n"
    )
    if output.returncode != 0:
        raise RuntimeError("SDK probe failed: %s" % output.stderr.strip())


def binary_dir(binaries):
    return Path(next(iter(binaries.values()))).parent


def scan_sanitized(output_dir):
    """Fail closed if anything marked synthetic leaked into frozen evidence.

    The rig sources that live beside the evidence are skipped: they are source,
    not captured bytes, and one of them names the forbidden markers in order to
    search for them.
    """
    forbidden = ["Authorization: Bearer", "sk-kairo-093", "api_key"]
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.parent.name == "rig":
            continue
        data = path.read_bytes()
        for needle in forbidden:
            if needle.encode() in data:
                raise AssertionError("unsanitized marker %r found in %s" % (needle, path))


def run(args):
    output_dir = Path(args.output).resolve() if args.output else Path(
        tempfile.mkdtemp(prefix="kairo-093-review-")
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    binaries = {
        "violation_gateway_iserror": Path(args.tag_binary).resolve(),
        "control_direct_stdio": Path(args.tag_binary).resolve(),
        "control_gateway_success": Path(args.tag_binary).resolve(),
        "differential_pr7640": Path(args.pr_binary).resolve(),
    }
    source_root = {
        "violation_gateway_iserror": Path(args.tag_source).resolve(),
        "control_direct_stdio": Path(args.tag_source).resolve(),
        "control_gateway_success": Path(args.tag_source).resolve(),
        "differential_pr7640": Path(args.pr_source).resolve(),
    }

    for cell_name in CELLS:
        commit = command_output(["git", "rev-parse", "HEAD"], source_root[cell_name])
        expected = PINNED_TAG if cell_name != "differential_pr7640" else PR_HEAD
        if commit != expected:
            raise AssertionError(
                "%s: expected %s, got %s" % (cell_name, expected, commit)
            )
        if not binaries[cell_name].is_file():
            raise AssertionError("%s: missing binary %s" % (cell_name, binaries[cell_name]))

    target = {
        "project": "maximhq/bifrost",
        "source": "https://github.com/maximhq/bifrost",
        "release_label": PINNED_TAG_LABEL,
        "release_commit": PINNED_TAG,
        "release_core_version": PINNED_TAG_CORE_VERSION,
        "pr_7640_head": PR_HEAD,
        "pr_7640_base": PR_BASE,
        "pr_7640_core_version": PR_HEAD_CORE_VERSION,
        "public_endpoint": "POST /mcp",
        "request_dialect": "MCP JSON-RPC over HTTP",
        "response_dialect": "MCP JSON-RPC over HTTP",
        "upstream_transport": "stdio MCP",
        "upstream_sdk": "github.com/mark3labs/mcp-go v0.43.2",
        "client_sdk": "github.com/mark3labs/mcp-go v0.43.2",
        "go_version": command_output(["go", "version"], source_root["violation_gateway_iserror"])
        .removeprefix("go version "),
        "runtime_prerequisites": {
            "docker": "unavailable in this environment; the pinned tag is built from source instead",
            "ui_embed_stub": "transports/bifrost-http/ui/index.html placeholder; the embedded UI is not on the /mcp request path",
            "pr_head_governance_pin": "PR #7640 head pins plugins/governance v1.8.3, whose published module lacks StartResetWorkers and does not compile; the pin was raised to the v1.8.4 the tag already pins. No Bifrost Go source under test was modified.",
        },
        "credentials": "none; no provider key, no Authorization header",
    }
    (output_dir / "target.json").write_text(json.dumps(target, indent=2) + "\n")

    results = {
        "complete": False,
        "runs_per_cell": args.runs,
        "target": target,
        "cells": {},
        "failures": [],
    }
    for cell_name, spec in CELLS.items():
        print("cell %s" % cell_name, flush=True)
        try:
            outcome = run_cell(
                cell_name, spec, args, binaries, output_dir, source_root
            )
        except Exception as error:  # noqa: BLE001 - recorded as a failure, not raised
            results["failures"].append("%s: %s" % (cell_name, error))
            print("  FAILED: %s" % error, file=sys.stderr, flush=True)
            continue
        results["cells"][cell_name] = outcome["summary"]
        print(
            "  isError key present per run: %s (%s)"
            % (
                outcome["summary"]["is_error_key_present_per_run"],
                outcome["summary"]["expectation_met_n_of_n"],
            ),
            flush=True,
        )

    run_sdk_probe(output_dir, binaries, args)
    results["complete"] = not results["failures"] and len(results["cells"]) == len(CELLS)
    if args.output:
        scan_sanitized(output_dir)
    (output_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps({"complete": results["complete"], "failures": results["failures"]}, indent=2))
    return 0 if results["complete"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag-source", required=True, help="Bifrost checkout at the pinned tag")
    parser.add_argument(
        "--tag-binary", default=None, help="built Bifrost binary from the tag"
    )
    parser.add_argument("--pr-source", required=True, help="Bifrost worktree at PR #7640 head")
    parser.add_argument(
        "--pr-binary", default=None, help="built Bifrost binary from the PR head"
    )
    parser.add_argument(
        "--rig-dir", required=True, help="directory holding the rig Go module sources"
    )
    parser.add_argument(
        "--bin-dir",
        required=True,
        help="directory the rig binaries and the Bifrost binaries are written to",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="build the pinned Bifrost binaries and the rig binaries first",
    )
    parser.add_argument(
        "--pr-head-governance-pin",
        default="v1.8.4",
        help="governance plugin module version to pin for the PR head build",
    )
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--output", default=None, help="freeze evidence here instead of a temp dir")
    args = parser.parse_args()
    rig = Path(args.rig_dir).resolve()
    bin_dir = Path(args.bin_dir).resolve()
    bin_dir.mkdir(parents=True, exist_ok=True)

    if args.build:
        build_rig(rig, bin_dir)
        tag_binary = bin_dir / ("bifrost-" + PINNED_TAG[:12])
        pr_binary = bin_dir / ("bifrost-pr-" + PR_HEAD[:12])
        build_bifrost(args.tag_source, tag_binary)
        # PR #7640 head pins plugins/governance v1.8.3, whose published module
        # does not carry StartResetWorkers, so that commit does not compile. The
        # pin is raised to the v1.8.4 the release tag already pins. No Go file
        # under test is touched, and build_bifrost re-checks that afterwards.
        subprocess.check_call(
            [
                "go", "mod", "edit",
                "-require=github.com/maximhq/bifrost/plugins/governance@" + args.pr_head_governance_pin,
            ],
            cwd=str(Path(args.pr_source) / "transports"),
        )
        subprocess.check_call(["go", "mod", "tidy"], cwd=str(Path(args.pr_source) / "transports"))
        build_bifrost(args.pr_source, pr_binary)
        args.tag_binary = str(tag_binary)
        args.pr_binary = str(pr_binary)

    for name in ("kairodirect", "kairoconsumer", "kairoserver", "kairotee", "kairosdkprobe"):
        if not (bin_dir / name).is_file():
            raise SystemExit("missing rig binary: %s" % (bin_dir / name))
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
