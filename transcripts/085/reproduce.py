#!/usr/bin/env python3
"""Reproduce LiteLLM's Responses-API mid-stream fallback replaying a delivered
tool call and splicing two stream lifecycles into one public SSE body.

The rig runs the real LiteLLM proxy over real HTTP, with a real `fallbacks`
router configuration. A deterministic OpenAI-compatible upstream
(upstream_server.py) supplies a primary deployment that completes a tool call
and then fails, and a fallback deployment that completes cleanly. A loopback
relay records the exact public request and response bytes consumed by the
official OpenAI Python SDK's `client.responses.stream()` accumulator.
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

# mode -> (public model_name the client requests, upstream fallback deployment
# reached through it). Deployment model names dispatch through
# upstream_server.provider_stream purely on the JSON body's `model` field.
MODES = {
    "trigger-duplicate-tool": "primary-tool",
    "trigger-sdk-crash-item-type": "primary-tool",
    "trigger-sdk-crash-partial-text": "primary-textpartial",
    "control-no-fault": "primary-nofault",
    "control-pre-first-chunk": "primary-prefail",
}
FALLBACK_FOR_MODE = {
    "trigger-duplicate-tool": "fallback-tool",
    "trigger-sdk-crash-item-type": "fallback-text",
    "trigger-sdk-crash-partial-text": "fallback-tool",
    "control-no-fault": "fallback-tool",
    "control-pre-first-chunk": "fallback-tool",
}
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
    require(not re.search(r"sk-[A-Za-z0-9_-]{24,}", text), "possible credential appeared in captured body")
    return text


def safe_headers(headers):
    return {name.lower(): value for name, value in headers if name.lower() in SAFE_HEADERS}


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
        self.server.append({
            "method": "POST",
            "path": self.path,
            "headers": safe_headers(self.headers.items()),
            "body_raw": safe_text(raw),
            "response_status": 200,
            "response_headers": {"content-type": "text/event-stream"},
            "response_body_raw": safe_text(response_raw),
        })
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(response_raw)))
        self.end_headers()
        self.wfile.write(response_raw)


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
        response_raw = response.read()
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
    for public_name, upstream_model in MODES.items():
        lines += [
            f"  - model_name: {public_name}",
            "    litellm_params:",
            f"      model: openai/{upstream_model}",
            f"      api_base: {base}",
            f"      api_key: {UPSTREAM_KEY}",
        ]
    for fallback_name in ("fallback-tool", "fallback-text"):
        lines += [
            f"  - model_name: {fallback_name}",
            "    litellm_params:",
            f"      model: openai/{fallback_name}",
            f"      api_base: {base}",
            f"      api_key: {UPSTREAM_KEY}",
        ]
    fallback_pairs = ", ".join(
        f'{{"{public}": ["{FALLBACK_FOR_MODE[public]}"]}}' for public in MODES
    )
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


def run_sdk_client(python, relay_port, mode):
    public_model = mode
    script = r'''
import json
import sys
import traceback
from openai import OpenAI

port, model = sys.argv[1:]
client = OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="sk-kairo-085-local-client", max_retries=0)
tools = [{"type": "function", "name": "append_ledger", "description": "Append one line to the ledger. Has a side effect.", "parameters": {"type": "object", "properties": {"line": {"type": "string"}}, "required": ["line"], "additionalProperties": False}, "strict": True}]
kwargs = {"model": model, "input": "Append the line 'run' to the ledger.", "tools": tools, "tool_choice": "required"}
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
'''
    completed = subprocess.run(
        [python, "-c", script, str(relay_port), public_model],
        check=True, capture_output=True, text=True, timeout=45,
    )
    return json.loads(completed.stdout)


def validate_record(record):
    require(record["client_request"]["path"] == "/v1/responses", "wrong public route")
    require(record["client_response"]["status"] == 200, "public route did not return 200")
    mode = record["mode"]
    consumer = record["consumer"]
    if mode == "trigger-duplicate-tool":
        require(len(record["upstream_exchanges"]) == 2, "duplicate-tool trigger did not fall back")
        require(consumer["error"] is None, "duplicate-tool trigger unexpectedly raised in the SDK")
        require(
            len(consumer["completed_tool_call_ids"]) == 2,
            f"expected 2 completed tool calls (the bug), got {consumer['completed_tool_call_ids']}",
        )
        require(len(consumer["created_ids"]) == 2, "expected two response.created events (the bug)")
    elif mode == "trigger-sdk-crash-item-type":
        require(len(record["upstream_exchanges"]) == 2, "item-type-crash trigger did not fall back")
        require(
            consumer["error"] is not None and consumer["error"]["type"] == "AssertionError",
            f"item-type-crash trigger did not crash the official SDK as claimed: {consumer['error']}",
        )
        require(consumer["final_response"] is None, "item-type-crash trigger unexpectedly returned a final response")
    elif mode == "trigger-sdk-crash-partial-text":
        require(len(record["upstream_exchanges"]) == 2, "partial-text-crash trigger did not fall back")
        require(
            consumer["error"] is not None and consumer["error"]["type"] == "AssertionError",
            f"partial-text-crash trigger did not crash the official SDK as claimed: {consumer['error']}",
        )
        require(consumer["final_response"] is None, "partial-text-crash trigger unexpectedly returned a final response")
    elif mode == "control-no-fault":
        require(len(record["upstream_exchanges"]) == 1, "no-fault control unexpectedly fell back")
        require(consumer["error"] is None, "no-fault control failed in the SDK")
        require(consumer["completed_tool_call_ids"] == ["call_primary"], "no-fault control lost its tool call")
        require(len(consumer["created_ids"]) == 1, "no-fault control produced more than one lifecycle")
    elif mode == "control-pre-first-chunk":
        require(len(record["upstream_exchanges"]) == 2, "pre-first-chunk control did not fall back")
        require(consumer["error"] is None, "pre-first-chunk control failed in the SDK")
        require(
            consumer["completed_tool_call_ids"] == ["call_fallback"],
            f"pre-first-chunk control should replay once and complete once, got {consumer['completed_tool_call_ids']}",
        )
    else:
        raise ReproductionError(f"unknown mode {mode!r}")


def run(args):
    output = Path(args.output_dir).resolve()
    require(not output.exists(), "output directory already exists")
    output.mkdir(parents=True)
    python = os.path.abspath(args.python)
    require(Path(python).is_file(), "--python does not name a Python executable")
    metadata = package_metadata(python)
    expected = args.expect_litellm
    require(metadata["packages"]["litellm"] == expected, f"expected LiteLLM {expected}")

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
                        relay_after = relay.snapshot()
                        upstream_after = upstream.snapshot()
                        require(len(relay_after) == relay_before + 1, "SDK did not make exactly one public request")
                        public_exchange = relay_after[-1]
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
                            "client_response": {
                                "status": public_exchange["response_status"],
                                "headers": public_exchange["response_headers"],
                                "body_raw": public_exchange["response_body_raw"],
                            },
                            "consumer": consumer,
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
        "complete": True,
    })
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"LiteLLM {expected}: all three triggers reproduced 5/5; both controls passed 5/5")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, help="Python executable in the pinned LiteLLM environment")
    parser.add_argument("--expect-litellm", required=True, help="exact installed LiteLLM version")
    parser.add_argument("--output-dir", required=True, help="fresh directory for sanitized captures")
    parser.add_argument("--captured-at", required=True, help="UTC date of this capture, e.g. 2026-09-24")
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, ReproductionError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(f"reproduction failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
