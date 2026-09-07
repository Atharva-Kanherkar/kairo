#!/usr/bin/env python3
"""Reproduce LiteLLM's MCP auto-execution Responses stream collision.

The rig runs the real LiteLLM proxy and a real stdio MCP server. A deterministic
OpenAI-compatible upstream supplies two valid provider Responses streams: a tool
call followed by a final text answer. A loopback relay records the exact public
request and response bytes consumed by the official OpenAI Python SDK.
"""

import argparse
import contextlib
import hashlib
import http.client
import http.server
import importlib.metadata
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


RUNS = 5
HOST = "127.0.0.1"
MASTER_KEY = "sk-kairo-074-local-master"
UPSTREAM_KEY = "sk-kairo-074-local-upstream"
CLIENT_KEY = "sk-kairo-074-local-client"
MODEL = "mock"
FINAL_TEXT = "FINAL_FROM_ROUND_2"
CONTROL_TEXT = "CONTROL_TEXT"
SAFE_HEADERS = {"content-type", "x-litellm-version"}
MODES = ("trigger", "approval", "no-tool")


class ReproductionError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ReproductionError(message)


def safe_text(raw):
    text = raw.decode("utf-8")
    for fixed_key in (MASTER_KEY, UPSTREAM_KEY, CLIENT_KEY):
        require(fixed_key not in text, "fixed local key appeared in captured body")
    require(
        not re.search(r"sk-[A-Za-z0-9_-]{24,}", text),
        "possible credential appeared in captured body",
    )
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
            with contextlib.suppress(Exception):
                if connection is not None:
                    connection.close()
        time.sleep(0.1)
    raise ReproductionError("LiteLLM did not become ready")


def response_object(response_id, status, output):
    return {
        "id": response_id,
        "object": "response",
        "created_at": 1_788_782_400,
        "status": status,
        "model": "mockmodel",
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 1,
            "output_tokens": 1,
            "total_tokens": 2,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def event(kind, data):
    return f"event: {kind}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def provider_stream(request):
    serialized = json.dumps(request, separators=(",", ":"))
    follow_up = "function_call_output" in serialized
    tools = request.get("tools") or []
    if follow_up or not tools:
        response_id = "resp_round_2"
        answer = FINAL_TEXT if follow_up else CONTROL_TEXT
        item = {
            "id": "msg_round_2",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": answer, "annotations": []}],
        }
        created = response_object(response_id, "in_progress", [])
        completed = response_object(response_id, "completed", [item])
        frames = [
            event("response.created", {"type": "response.created", "response": created, "sequence_number": 0}),
            event("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": {**item, "status": "in_progress", "content": []}, "sequence_number": 1}),
            event("response.content_part.added", {"type": "response.content_part.added", "item_id": item["id"], "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}, "sequence_number": 2}),
            event("response.output_text.delta", {"type": "response.output_text.delta", "item_id": item["id"], "output_index": 0, "content_index": 0, "delta": answer, "sequence_number": 3}),
            event("response.output_text.done", {"type": "response.output_text.done", "item_id": item["id"], "output_index": 0, "content_index": 0, "text": answer, "sequence_number": 4}),
            event("response.content_part.done", {"type": "response.content_part.done", "item_id": item["id"], "output_index": 0, "content_index": 0, "part": item["content"][0], "sequence_number": 5}),
            event("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item, "sequence_number": 6}),
            event("response.completed", {"type": "response.completed", "response": completed, "sequence_number": 7}),
        ]
        return "".join(frames)

    response_id = "resp_round_1"
    tool_name = next(
        (tool.get("name") for tool in tools if tool.get("type") == "function"),
        "echo",
    )
    item = {
        "id": "fc_round_1",
        "type": "function_call",
        "call_id": "call_round_1",
        "name": tool_name,
        "arguments": '{"value":"PING"}',
        "status": "completed",
    }
    created = response_object(response_id, "in_progress", [])
    completed = response_object(response_id, "completed", [item])
    frames = [
        event("response.created", {"type": "response.created", "response": created, "sequence_number": 0}),
        event("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": {**item, "arguments": "", "status": "in_progress"}, "sequence_number": 1}),
        event("response.function_call_arguments.delta", {"type": "response.function_call_arguments.delta", "item_id": item["id"], "output_index": 0, "delta": item["arguments"], "sequence_number": 2}),
        event("response.function_call_arguments.done", {"type": "response.function_call_arguments.done", "item_id": item["id"], "output_index": 0, "arguments": item["arguments"], "sequence_number": 3}),
        event("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item, "sequence_number": 4}),
        event("response.completed", {"type": "response.completed", "response": completed, "sequence_number": 5}),
    ]
    return "".join(frames)


class CaptureServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler):
        super().__init__(address, handler)
        self.records = []
        self.lock = threading.Lock()

    def append(self, record):
        with self.lock:
            self.records.append(record)

    def snapshot(self):
        with self.lock:
            return list(self.records)


class UpstreamHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        body = b'{"object":"list","data":[{"id":"mockmodel","object":"model"}]}'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length", "0")))
        request = json.loads(raw)
        response_raw = provider_stream(request).encode()
        self.server.append(
            {
                "method": "POST",
                "path": self.path,
                "headers": safe_headers(self.headers.items()),
                "body_raw": safe_text(raw),
                "response_status": 200,
                "response_headers": {"content-type": "text/event-stream"},
                "response_body_raw": safe_text(response_raw),
            }
        )
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(response_raw)))
        self.end_headers()
        self.wfile.write(response_raw)


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
        self.server.append(
            {
                "method": "POST",
                "path": self.path,
                "headers": safe_headers(self.headers.items()),
                "body_raw": safe_text(request_raw),
                "response_status": response.status,
                "response_headers": safe_headers(response_headers),
                "response_body_raw": safe_text(response_raw),
            }
        )
        self.send_response(response.status)
        self.send_header(
            "content-type",
            dict((name.lower(), value) for name, value in response_headers).get(
                "content-type", "text/event-stream"
            ),
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


def write_config(path, python, mcp_server, call_log, upstream_port):
    path.write_text(
        "\n".join(
            [
                "model_list:",
                "  - model_name: mock",
                "    litellm_params:",
                "      model: openai/mockmodel",
                f"      api_base: http://{HOST}:{upstream_port}/v1",
                f"      api_key: {UPSTREAM_KEY}",
                "mcp_servers:",
                "  demo:",
                "    transport: stdio",
                f"    command: {python}",
                "    args:",
                f"      - {mcp_server}",
                f"      - {call_log}",
                "    description: deterministic local echo tool",
                "general_settings:",
                f"  master_key: {MASTER_KEY}",
                "litellm_settings:",
                "  telemetry: false",
                "",
            ]
        ),
        encoding="utf-8",
    )


def package_metadata(python):
    script = (
        "import importlib.metadata,json;"
        "print(json.dumps({n:importlib.metadata.version(n) for n in ['litellm','openai','mcp']}))"
    )
    versions = json.loads(subprocess.check_output([python, "-c", script], text=True))
    source_script = (
        "from pathlib import Path;import importlib;"
        "m=importlib.import_module('litellm.responses.mcp.mcp_streaming_iterator');"
        "print(Path(m.__file__).resolve())"
    )
    source = Path(subprocess.check_output([python, "-c", source_script], text=True).strip())
    return {
        "packages": versions,
        "iterator_source": "litellm/responses/mcp/mcp_streaming_iterator.py",
        "iterator_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "python": subprocess.check_output([python, "--version"], text=True).strip(),
    }


def client_kwargs(mode):
    kwargs = {
        "model": MODEL,
        "input": (
            "Use the echo tool with PING, then answer."
            if mode != "no-tool"
            else "Answer without tools."
        ),
    }
    if mode != "no-tool":
        kwargs["tools"] = [
            {
                "type": "mcp",
                "server_url": "litellm_proxy/mcp/demo",
                "server_label": "demo",
                "require_approval": "never" if mode == "trigger" else "always",
            }
        ]
        kwargs["tool_choice"] = "required"
    return kwargs


def run_sdk_client(python, relay_port, mode):
    script = r'''
import json
import sys
import traceback
from openai import OpenAI

port, mode = sys.argv[1:]
client = OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="sk-kairo-074-local-client", max_retries=0)
kwargs = {"model":"mock","input":"Use the echo tool with PING, then answer." if mode != "no-tool" else "Answer without tools."}
if mode != "no-tool":
    kwargs["tools"] = [{"type":"mcp","server_url":"litellm_proxy/mcp/demo","server_label":"demo","require_approval":"never" if mode == "trigger" else "always"}]
    kwargs["tool_choice"] = "required"
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
    error = {"type":type(exc).__name__,"message":str(exc),"last_frame":{"file":frame.filename.rsplit("/site-packages/",1)[-1],"line":frame.lineno,"function":frame.name} if frame else None}
print(json.dumps({"event_types":[x.get("type") for x in events],"created_ids":[x["response"]["id"] for x in events if x.get("type")=="response.created"],"completed_ids":[x["response"]["id"] for x in events if x.get("type")=="response.completed"],"final_response":final,"error":error},separators=(",",":")))
'''
    completed = subprocess.run(
        [python, "-c", script, str(relay_port), mode],
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
    )
    return json.loads(completed.stdout)


def validate_record(record):
    require(record["client_request"]["path"] == "/v1/responses", "wrong public route")
    require(record["client_response"]["status"] == 200, "public route did not return 200")
    require(len(record["upstream_exchanges"]) in (1, 2), "unexpected provider call count")
    mode = record["mode"]
    consumer = record["consumer"]
    calls = record["mcp_calls"]
    if mode == "trigger":
        require(len(record["upstream_exchanges"]) == 2, "trigger did not make a follow-up model call")
        require(len(calls) == 1 and calls[0] == {"tool": "echo", "value": "PING"}, "trigger did not execute MCP exactly once")
        require(consumer["error"] and consumer["error"]["type"] == "AssertionError", "trigger did not crash SDK as claimed")
        require(consumer["final_response"] is None, "trigger unexpectedly returned a final SDK response")
    elif mode == "approval":
        require(len(record["upstream_exchanges"]) == 1, "approval control made a follow-up model call")
        require(not calls, "approval control executed MCP")
        require(consumer["error"] is None, "approval control failed in SDK")
        require([x["type"] for x in consumer["final_response"]["output"]] == ["function_call"], "approval control returned wrong output")
    else:
        require(len(record["upstream_exchanges"]) == 1, "no-tool control made extra model calls")
        require(not calls, "no-tool control executed MCP")
        require(consumer["error"] is None, "no-tool control failed in SDK")
        require(
            consumer["final_response"]["output"][0]["content"][0]["text"] == CONTROL_TEXT,
            "no-tool control lost text",
        )


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

    with tempfile.TemporaryDirectory(prefix="kairo-074-") as temp_name:
        temp = Path(temp_name)
        config = temp / "config.yaml"
        call_log = temp / "mcp-calls.jsonl"
        call_log.touch()
        mcp_server = Path(__file__).with_name("mcp_server.py").resolve()
        write_config(config, python, mcp_server, call_log, upstream_port)
        log_path = temp / "litellm.log"
        log_stream = log_path.open("w", encoding="utf-8")
        process = None
        upstream = CaptureServer((HOST, upstream_port), UpstreamHandler)
        relay = RelayServer((HOST, relay_port), proxy_port)
        try:
            with serving(upstream), serving(relay):
                litellm_bin = str(Path(python).with_name("litellm"))
                require(Path(litellm_bin).is_file(), "the Python environment has no litellm executable")
                process = subprocess.Popen(
                    [litellm_bin, "--config", str(config), "--port", str(proxy_port)],
                    stdout=log_stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                wait_ready(proxy_port, process)
                records = []
                for mode in MODES:
                    for trial in range(1, RUNS + 1):
                        relay_before = len(relay.snapshot())
                        upstream_before = len(upstream.snapshot())
                        calls_before = call_log.read_text(encoding="utf-8").splitlines()
                        consumer = run_sdk_client(python, relay_port, mode)
                        relay_after = relay.snapshot()
                        upstream_after = upstream.snapshot()
                        calls_after = call_log.read_text(encoding="utf-8").splitlines()
                        require(len(relay_after) == relay_before + 1, "SDK did not make exactly one public request")
                        new_calls = [json.loads(line) for line in calls_after[len(calls_before):]]
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
                            "mcp_calls": new_calls,
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
    metadata.update(
        {
            "captured_at": "2026-09-07",
            "runs_per_mode": RUNS,
            "public_endpoint": "/v1/responses",
            "backend": "deterministic local OpenAI Responses capture upstream",
            "mcp_transport": "stdio",
            "complete": True,
        }
    )
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"LiteLLM {expected}: trigger failed 5/5; approval and no-tool controls passed 5/5")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, help="Python executable in the pinned LiteLLM environment")
    parser.add_argument("--expect-litellm", required=True, help="exact installed LiteLLM version")
    parser.add_argument("--output-dir", required=True, help="fresh directory for sanitized captures")
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, ReproductionError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(f"reproduction failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
