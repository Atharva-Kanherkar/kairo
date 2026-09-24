#!/usr/bin/env python3
"""Reproduce LiteLLM's Responses-API mid-stream fallback replaying a delivered
tool call and splicing two stream lifecycles into one public SSE body.

The rig runs the real LiteLLM proxy over real HTTP, with a real `fallbacks`
router configuration. A deterministic OpenAI-compatible upstream
(upstream_server.py) supplies primary deployments that stream some output and
then fail, and fallback deployments that complete cleanly. A loopback relay
records the exact public request and response bytes consumed by the official
OpenAI Python SDK's `client.responses.stream()` accumulator. Each captured
public body is then replayed, byte for byte, to the same consumer under every
`--sdk-python` interpreter, so the result is recorded per SDK version.
"""

import argparse
import contextlib
import hashlib
import http.client
import http.server
import importlib.metadata
import importlib.util
import itertools
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time

_SPEC = importlib.util.spec_from_file_location(
    "kairo_085_upstream_server", Path(__file__).with_name("upstream_server.py")
)
upstream_server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(upstream_server)

RUNS = 5
HOST = "127.0.0.1"
MASTER_KEY = "sk-kairo-085-local-master"
UPSTREAM_KEY = "sk-kairo-085-local-upstream"
CLIENT_KEY = "sk-kairo-085-local-client"
SAFE_HEADERS = {"content-type", "x-litellm-version"}
CREDENTIAL_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{24,}")

# mode -> (primary upstream model, fallback upstream model, tool_choice). The
# public model_name the client requests is the mode itself. Deployment model
# names dispatch through upstream_server.provider_stream purely on the JSON
# body's `model` field.
MODES = {
    "trigger-duplicate-tool": ("primary-tool", "fallback-tool", "required"),
    "trigger-duplicate-tool-response-failed": ("primary-tool-failed", "fallback-tool", "required"),
    "trigger-sdk-crash-item-type": ("primary-tool", "fallback-reasoning-tool", "required"),
    "trigger-sdk-crash-partial-text": ("primary-textpartial", "fallback-tool", "auto"),
    "control-no-fault": ("primary-nofault", "fallback-tool", "required"),
    "control-fault-before-output": ("primary-prefail", "fallback-tool", "required"),
    "boundary-announced-only": ("primary-announced", "fallback-tool", "required"),
    "boundary-transport-drop": ("primary-tool-drop", "fallback-tool", "required"),
}
FALLBACK_DEPLOYMENTS = ("fallback-tool", "fallback-reasoning-tool")
# Modes whose fallback request must carry the client's original input
# unchanged. The partial-text mode goes through LiteLLM's continuation path.
REPLAY_MODES = {
    "trigger-duplicate-tool", "trigger-duplicate-tool-response-failed", "trigger-sdk-crash-item-type",
    "control-fault-before-output", "boundary-announced-only",
}
# openai-python 3.14.0 ("preserve response stream indexes after empty items",
# openai/openai-python#3126) re-keys the stream accumulator by output_index.
SDK_INDEX_FIX = (3, 14, 0)
DROP_MISSING_BYTES = 4096
TOOLS = [{
    "type": "function", "name": "append_ledger",
    "description": "Append one line to the ledger. Has a side effect.",
    "parameters": {"type": "object", "properties": {"line": {"type": "string"}},
                   "required": ["line"], "additionalProperties": False},
    "strict": True,
}]


class ReproductionError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ReproductionError(message)


def safe_text(raw):
    text = raw.decode("utf-8")
    for fixed_key in (MASTER_KEY, UPSTREAM_KEY, CLIENT_KEY):
        require(fixed_key not in text, "fixed local key appeared in captured body")
    # Anchored at a token boundary: LiteLLM's random base64url response ids can
    # contain "sk-" mid-string, which is not a credential.
    require(not CREDENTIAL_PATTERN.search(text), "possible credential appeared in captured body")
    return text


def safe_headers(headers):
    return {name.lower(): value for name, value in headers if name.lower() in SAFE_HEADERS}


def version_tuple(version):
    return tuple(int(part) for part in version.split(".")[:3])


def free_port():
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def wait_ready(port, process, timeout=45):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise ReproductionError(f"LiteLLM exited early with status {process.returncode}")
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
        time.sleep(0.5)
    raise ReproductionError("LiteLLM proxy did not become ready")


class CaptureServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler_cls):
        super().__init__(address, handler_cls)
        self._records = []
        self._lock = threading.Lock()

    def append(self, record):
        with self._lock:
            self._records.append(record)

    def snapshot(self):
        with self._lock:
            return list(self._records)


class UpstreamHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length", "0")))
        request = json.loads(raw)
        response_raw = upstream_server.provider_stream(request, self.server.call_counter)
        drop = upstream_server.drops_connection(request.get("model", ""))
        self.server.append({
            "method": "POST",
            "path": self.path,
            "headers": safe_headers(self.headers.items()),
            "body_raw": safe_text(raw),
            "response_status": 200,
            "response_headers": {"content-type": "text/event-stream"},
            "response_body_raw": safe_text(response_raw),
            "transport": "closed-before-declared-length" if drop else "complete",
        })
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        # A dropped connection declares more bytes than it sends, then closes.
        self.send_header("content-length", str(len(response_raw) + (DROP_MISSING_BYTES if drop else 0)))
        self.end_headers()
        self.wfile.write(response_raw)
        self.wfile.flush()
        if drop:
            time.sleep(0.3)
            self.close_connection = True
            with contextlib.suppress(OSError):
                self.connection.shutdown(socket.SHUT_RDWR)


class UpstreamServer(CaptureServer):
    def __init__(self, address):
        super().__init__(address, UpstreamHandler)
        self.call_counter = itertools.count(1)


class RelayHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        request_raw = self.rfile.read(int(self.headers.get("content-length", "0")))
        connection = http.client.HTTPConnection(HOST, self.server.proxy_port, timeout=30)
        forwarded_headers = {
            "content-type": self.headers.get("content-type", "application/json"),
            "authorization": f"Bearer {MASTER_KEY}",
        }
        connection.request("POST", self.path, body=request_raw, headers=forwarded_headers)
        response = connection.getresponse()
        incomplete = False
        try:
            response_raw = response.read()
        except http.client.IncompleteRead as exc:
            response_raw, incomplete = exc.partial, True
        response_headers = response.getheaders()
        connection.close()
        self.server.append({
            "method": "POST",
            "path": self.path,
            "headers": safe_headers(self.headers.items()),
            "body_raw": safe_text(request_raw),
            "response_status": response.status,
            "response_headers": safe_headers(response_headers),
            "response_body_raw": safe_text(response_raw),
            "response_incomplete": incomplete,
        })
        self.send_response(response.status)
        self.send_header(
            "content-type",
            dict((n.lower(), v) for n, v in response_headers).get("content-type", "text/event-stream"),
        )
        self.send_header("content-length", str(len(response_raw)))
        self.end_headers()
        self.wfile.write(response_raw)


class RelayServer(CaptureServer):
    def __init__(self, address, proxy_port):
        super().__init__(address, RelayHandler)
        self.proxy_port = proxy_port


class ReplayHandler(http.server.BaseHTTPRequestHandler):
    """Serves one captured public response, byte for byte, to an SDK consumer."""

    def log_message(self, *_args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", "0")))
        status, content_type, body = self.server.response
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ReplayServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, response):
        super().__init__(address, ReplayHandler)
        self.response = response


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


def write_config(path, upstream_port):
    base = f"http://{HOST}:{upstream_port}/v1"
    lines = ["model_list:"]
    for public_name, (primary, _fallback, _tool_choice) in MODES.items():
        lines += [
            f"  - model_name: {public_name}",
            "    litellm_params:",
            f"      model: openai/{primary}",
            f"      api_base: {base}",
            f"      api_key: {UPSTREAM_KEY}",
        ]
    for fallback_name in FALLBACK_DEPLOYMENTS:
        lines += [
            f"  - model_name: {fallback_name}",
            "    litellm_params:",
            f"      model: openai/{fallback_name}",
            f"      api_base: {base}",
            f"      api_key: {UPSTREAM_KEY}",
        ]
    fallback_pairs = ", ".join(f'{{"{public}": ["{fallback}"]}}' for public, (_, fallback, _) in MODES.items())
    lines += [
        "router_settings:",
        f"  fallbacks: [{fallback_pairs}]",
        "  num_retries: 0",
        "general_settings:",
        f"  master_key: {MASTER_KEY}",
        "litellm_settings:",
        "  telemetry: false",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def package_metadata(python):
    script = (
        "import importlib.metadata,json;"
        "print(json.dumps({n:importlib.metadata.version(n) for n in ['litellm','openai']}))"
    )
    versions = json.loads(subprocess.check_output([python, "-c", script], text=True))
    source_script = (
        "from pathlib import Path;import importlib;"
        "m=importlib.import_module('litellm.router');"
        "print(Path(m.__file__).resolve())"
    )
    source = Path(subprocess.check_output([python, "-c", source_script], text=True).strip())
    return {
        "packages": versions,
        "iterator_source": "litellm/router.py (_aresponses_streaming_iterator)",
        "iterator_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "python": subprocess.check_output([python, "--version"], text=True).strip(),
    }


def openai_version(python):
    script = "import importlib.metadata;print(importlib.metadata.version('openai'))"
    return subprocess.check_output([python, "-c", script], text=True).strip()


CONSUMER_SCRIPT = r"""
import json
import sys
import traceback
from openai import OpenAI

port, model, tool_choice = sys.argv[1:]
client = OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="sk-kairo-085-local-client", max_retries=0)
tools = [{"type": "function", "name": "append_ledger", "description": "Append one line to the ledger. Has a side effect.", "parameters": {"type": "object", "properties": {"line": {"type": "string"}}, "required": ["line"], "additionalProperties": False}, "strict": True}]
kwargs = {"model": model, "input": "Append the line 'run' to the ledger.", "tools": tools, "tool_choice": tool_choice}
events = []
final = None
error = None
try:
    with client.responses.stream(**kwargs) as stream:
        for item in stream:
            events.append(item.model_dump(mode="json"))
        final = stream.get_final_response().model_dump(mode="json")
except BaseException as exc:
    stack = traceback.extract_tb(exc.__traceback__)
    frame = stack[-1] if stack else None
    error = {"type": type(exc).__name__, "message": str(exc), "last_frame": {"file": frame.filename.rsplit("/site-packages/", 1)[-1], "line": frame.lineno, "function": frame.name} if frame else None}
done_calls = [e["item"]["call_id"] for e in events if e.get("type") == "response.output_item.done" and e.get("item", {}).get("type") == "function_call"]
print(json.dumps({
    "event_types": [x.get("type") for x in events],
    "created_ids": [x["response"]["id"] for x in events if x.get("type") == "response.created"],
    "completed_tool_call_ids": done_calls,
    "final_response": final,
    "error": error,
}, separators=(",", ":")))
"""


def run_sdk_client(python, port, mode):
    completed = subprocess.run(
        [python, "-c", CONSUMER_SCRIPT, str(port), mode, MODES[mode][2]],
        check=True, capture_output=True, text=True, timeout=45,
    )
    return json.loads(completed.stdout)


def replay_to_sdks(sdk_pythons, mode, client_response):
    """Replay one captured public response to the consumer under every
    additional SDK interpreter, byte for byte."""
    headers = client_response["headers"]
    response = (
        client_response["status"],
        headers.get("content-type", "text/event-stream"),
        client_response["body_raw"].encode("utf-8"),
    )
    results = []
    for sdk_python, sdk_version in sdk_pythons:
        with serving(ReplayServer((HOST, 0), response)) as replay:
            consumer = run_sdk_client(sdk_python, replay.server_address[1], mode)
        results.append({"openai": sdk_version, "consumer": consumer})
    return results


def expect_consumer(mode, openai, consumer):
    """The SDK outcome each mode must produce, as a function of the SDK version."""
    label = f"{mode} under openai {openai}"
    error = consumer["error"]
    calls = consumer["completed_tool_call_ids"]
    fixed = version_tuple(openai) >= SDK_INDEX_FIX
    if mode in ("trigger-duplicate-tool", "trigger-duplicate-tool-response-failed"):
        require(error is None, f"{label}: unexpectedly raised {error}")
        require(calls == ["call_primary", "call_fallback"], f"{label}: expected the tool call twice (the bug), got {calls}")
        require(len(consumer["created_ids"]) == 2, f"{label}: expected two response.created events (the bug)")
    elif mode in ("trigger-sdk-crash-item-type", "trigger-sdk-crash-partial-text"):
        if fixed:
            require(error is None, f"{label}: openai >= 3.14 should tolerate the reused index, got {error}")
            require(consumer["final_response"] is not None, f"{label}: no final response")
            expected = ["call_primary", "call_fallback"] if mode == "trigger-sdk-crash-item-type" else ["call_fallback"]
            require(calls == expected, f"{label}: expected {expected}, got {calls}")
        else:
            require(error is not None and error["type"] == "AssertionError", f"{label}: did not crash the SDK as claimed: {error}")
            require(consumer["final_response"] is None, f"{label}: unexpectedly returned a final response")
    elif mode == "control-no-fault":
        require(error is None, f"{label}: failed in the SDK")
        require(calls == ["call_primary"], f"{label}: lost or duplicated its tool call: {calls}")
        require(len(consumer["created_ids"]) == 1, f"{label}: produced more than one lifecycle")
    elif mode in ("control-fault-before-output", "boundary-announced-only"):
        require(error is None, f"{label}: failed in the SDK")
        require(calls == ["call_fallback"], f"{label}: should complete exactly the fallback's call, got {calls}")
    elif mode == "boundary-transport-drop":
        require(error is not None, f"{label}: a transport drop must surface an error, not a completed stream")
        require(consumer["final_response"] is None, f"{label}: unexpectedly returned a final response")
        require("call_fallback" not in calls, f"{label}: the fallback must not have run")
    else:
        raise ReproductionError(f"unknown mode {mode!r}")


def validate_record(record):
    require(record["client_request"]["path"] == "/v1/responses", "wrong public route")
    mode = record["mode"]
    require(mode in MODES, f"unknown mode {mode!r}")
    request = json.loads(record["client_request"]["body_raw"])
    require(request.get("tool_choice") == MODES[mode][2], f"{mode}: wrong tool_choice in the client request")
    upstream = record["upstream_exchanges"]
    status = record["client_response"]["status"]
    if mode == "boundary-transport-drop":
        require(len(upstream) == 1, "a transport drop must not reach the fallback deployment")
        require(upstream[0].get("transport") == "closed-before-declared-length", "the primary did not drop")
    else:
        require(status == 200, f"{mode}: public route did not return 200")
        expected_calls = 1 if mode == "control-no-fault" else 2
        require(len(upstream) == expected_calls, f"{mode}: expected {expected_calls} upstream calls, got {len(upstream)}")
    if mode in REPLAY_MODES:
        fallback_input = json.loads(upstream[1]["body_raw"]).get("input")
        require(fallback_input == request.get("input"), f"{mode}: the fallback did not receive the original input")
    if mode == "trigger-sdk-crash-partial-text":
        fallback_input = json.loads(upstream[1]["body_raw"]).get("input")
        require(isinstance(fallback_input, list) and any(
            isinstance(item, dict) and item.get("role") == "developer" for item in fallback_input
        ), "partial-text trigger did not take LiteLLM's continuation path")
    expect_consumer(mode, record["consumer_openai"], record["consumer"])
    for replay in record.get("sdk_replays", []):
        expect_consumer(mode, replay["openai"], replay["consumer"])


def run(args):
    output = Path(args.output_dir).resolve()
    require(not output.exists(), "output directory already exists")
    output.mkdir(parents=True)
    python = os.path.abspath(args.python)
    require(Path(python).is_file(), "--python does not name a Python executable")
    metadata = package_metadata(python)
    expected = args.expect_litellm
    require(metadata["packages"]["litellm"] == expected, f"expected LiteLLM {expected}")
    sdk_pythons = [(os.path.abspath(p), openai_version(os.path.abspath(p))) for p in args.sdk_python or []]

    proxy_port, upstream_port, relay_port = free_port(), free_port(), free_port()
    while len({proxy_port, upstream_port, relay_port}) != 3:
        proxy_port, upstream_port, relay_port = free_port(), free_port(), free_port()

    with tempfile.TemporaryDirectory(prefix="kairo-085-") as temp_name:
        temp = Path(temp_name)
        config = temp / "config.yaml"
        write_config(config, upstream_port)
        log_path = temp / "litellm.log"
        log_stream = log_path.open("w", encoding="utf-8")
        process = None
        upstream = UpstreamServer((HOST, upstream_port))
        relay = RelayServer((HOST, relay_port), proxy_port)
        records = []
        try:
            with serving(upstream), serving(relay):
                litellm_bin = str(Path(python).with_name("litellm"))
                require(Path(litellm_bin).is_file(), "the Python environment has no litellm executable")
                process = subprocess.Popen(
                    [litellm_bin, "--config", str(config), "--port", str(proxy_port)],
                    stdout=log_stream, stderr=subprocess.STDOUT, text=True,
                )
                wait_ready(proxy_port, process)
                for mode in MODES:
                    for trial in range(1, RUNS + 1):
                        relay_before = len(relay.snapshot())
                        upstream_before = len(upstream.snapshot())
                        consumer = run_sdk_client(python, relay_port, mode)
                        time.sleep(0.4 if mode == "boundary-transport-drop" else 0)
                        relay_after = relay.snapshot()
                        upstream_after = upstream.snapshot()
                        require(len(relay_after) == relay_before + 1, "SDK did not make exactly one public request")
                        public_exchange = relay_after[-1]
                        client_response = {
                            "status": public_exchange["response_status"],
                            "headers": public_exchange["response_headers"],
                            "body_raw": public_exchange["response_body_raw"],
                            "incomplete": public_exchange["response_incomplete"],
                        }
                        record = {
                            "target": {"project": "BerriAI/litellm", "version": expected},
                            "mode": mode,
                            "trial": trial,
                            "client_request": {
                                "method": public_exchange["method"],
                                "path": public_exchange["path"],
                                "headers": public_exchange["headers"],
                                "body_raw": public_exchange["body_raw"],
                            },
                            "upstream_exchanges": upstream_after[upstream_before:],
                            "client_response": client_response,
                            "consumer_openai": metadata["packages"]["openai"],
                            "consumer": consumer,
                            "sdk_replays": replay_to_sdks(sdk_pythons, mode, client_response),
                        }
                        validate_record(record)
                        records.append(record)
        finally:
            if process is not None:
                process.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=10)
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            log_stream.close()

    for mode in MODES:
        path = output / f"{mode}.jsonl"
        selected = [record for record in records if record["mode"] == mode]
        path.write_text(
            "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in selected),
            encoding="utf-8",
        )
    metadata.update({
        "captured_at": args.captured_at,
        "runs_per_mode": RUNS,
        "public_endpoint": "/v1/responses",
        "backend": "deterministic local OpenAI Responses capture upstream",
        "sdk_replays": [version for _, version in sdk_pythons],
        "note": args.note,
        "complete": True,
    })
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"LiteLLM {expected}: every mode matched its expected outcome {RUNS}/{RUNS}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, help="Python executable in the pinned LiteLLM environment")
    parser.add_argument("--expect-litellm", required=True, help="exact installed LiteLLM version")
    parser.add_argument("--output-dir", required=True, help="fresh directory for sanitized captures")
    parser.add_argument("--captured-at", required=True, help="UTC date of this capture, e.g. 2026-09-24")
    parser.add_argument("--sdk-python", action="append", default=[],
                        help="extra Python executable with another openai version; each capture is replayed to it")
    parser.add_argument("--note", help="free-text provenance recorded in metadata.json, e.g. the source commit")
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, ReproductionError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(f"reproduction failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
