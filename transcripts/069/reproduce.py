#!/usr/bin/env python3
"""Reproduce Switchyard OpenAI Chat to Responses refusal type loss.

The script runs a real switchyard-server binary against a deterministic local
OpenAI Chat upstream. It saves raw request and response bodies for five runs of
each buffered, streaming, and same-dialect control path. No provider credential
is read or required.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MODEL = "captured-model"
REFUSAL = "REFUSALPROBE cannot help"
PROMPT = "REFUSALPROBE trigger"


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def chat_buffered() -> bytes:
    return json_bytes(
        {
            "id": "chatcmpl-refusal-069",
            "object": "chat.completion",
            "created": 1788541200,
            "model": MODEL,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "refusal": REFUSAL,
                        "annotations": [],
                    },
                    "logprobs": None,
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 4,
                "total_tokens": 8,
            },
        }
    )


def chat_stream() -> bytes:
    base = {
        "id": "chatcmpl-refusal-069",
        "object": "chat.completion.chunk",
        "created": 1788541200,
        "model": MODEL,
    }
    chunks = [
        {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": None, "refusal": ""}, "finish_reason": None}]},
        {**base, "choices": [{"index": 0, "delta": {"refusal": REFUSAL}, "finish_reason": None}]},
        {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    return ("\n\n".join(f"data: {json.dumps(chunk, separators=(',', ':'))}" for chunk in chunks) + "\n\ndata: [DONE]\n\n").encode()


class CaptureServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), CaptureHandler)
        self.daemon_threads = True
        self.exchanges: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def add_exchange(self, exchange: dict[str, Any]) -> None:
        with self.lock:
            self.exchanges.append(exchange)


class CaptureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: Any) -> None:
        return

    def do_POST(self) -> None:
        server: CaptureServer = self.server  # type: ignore[assignment]
        request_raw = self.rfile.read(int(self.headers.get("content-length", "0")))
        request = json.loads(request_raw)
        streaming = bool(request.get("stream"))
        response_raw = chat_stream() if streaming else chat_buffered()
        server.add_exchange(
            {
                "method": "POST",
                "path": self.path,
                "content_type": self.headers.get("content-type"),
                "body_raw": request_raw.decode(),
                "response_status": 200,
                "response_content_type": (
                    "text/event-stream; charset=utf-8"
                    if streaming
                    else "application/json"
                ),
                "response_body_raw": response_raw.decode(),
            }
        )
        self.send_response(200)
        self.send_header(
            "content-type",
            "text/event-stream; charset=utf-8" if streaming else "application/json",
        )
        if streaming:
            self.send_header("connection", "close")
        else:
            self.send_header("content-length", str(len(response_raw)))
        self.end_headers()
        self.wfile.write(response_raw)
        self.wfile.flush()
        if streaming:
            self.close_connection = True


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_port(port: int, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError(f"Switchyard did not listen on port {port}")


def sse_events(raw: str) -> list[dict[str, Any]]:
    events = []
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if not data or data == "[DONE]":
            continue
        events.append(json.loads(data))
    return events


def responses_consumer(raw: str, streaming: bool) -> dict[str, Any]:
    if streaming:
        events = sse_events(raw)
        event_types = [event.get("type") for event in events]
        refusal = "".join(
            event.get("delta", "")
            for event in events
            if event.get("type") == "response.refusal.delta"
        )
        done_refusals = [
            event.get("refusal")
            for event in events
            if event.get("type") == "response.refusal.done"
        ]
        output_text = "".join(
            event.get("delta", "")
            for event in events
            if event.get("type") == "response.output_text.delta"
        )
        return {
            "classified_as_refusal": bool(refusal) and REFUSAL in done_refusals,
            "refusal_text": refusal,
            "ordinary_output_text": output_text,
            "event_types": event_types,
        }

    body = json.loads(raw)
    parts = [
        part
        for item in body.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
    ]
    typed = [part.get("refusal") for part in parts if part.get("type") == "refusal"]
    ordinary = [part.get("text") for part in parts if part.get("type") == "output_text"]
    return {
        "classified_as_refusal": REFUSAL in typed,
        "refusal_text": "".join(value for value in typed if isinstance(value, str)),
        "ordinary_output_text": "".join(value for value in ordinary if isinstance(value, str)),
        "content_types": [part.get("type") for part in parts],
    }


def chat_consumer(raw: str, streaming: bool) -> dict[str, Any]:
    if streaming:
        events = sse_events(raw)
        refusal = "".join(
            choice.get("delta", {}).get("refusal", "")
            for event in events
            for choice in event.get("choices", [])
        )
        return {
            "classified_as_refusal": refusal == REFUSAL,
            "refusal_text": refusal,
        }
    body = json.loads(raw)
    refusal = body["choices"][0]["message"].get("refusal")
    return {
        "classified_as_refusal": refusal == REFUSAL,
        "refusal_text": refusal,
    }


def post(port: int, path: str, payload: dict[str, Any]) -> tuple[int, str, str]:
    request_raw = json.dumps(payload, separators=(",", ":"))
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        connection.request(
            "POST",
            path,
            body=request_raw,
            headers={"content-type": "application/json"},
        )
        response = connection.getresponse()
        return response.status, response.getheader("content-type", ""), response.read().decode()
    finally:
        connection.close()


def request_for(path: str, streaming: bool) -> dict[str, Any]:
    if path == "/v1/responses":
        request: dict[str, Any] = {
            "model": "main",
            "input": PROMPT,
            "max_output_tokens": 32,
        }
    else:
        request = {
            "model": "main",
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": 32,
        }
    if streaming:
        request["stream"] = True
    return request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--switchyard-server", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()

    if not os.access(args.switchyard_server, os.X_OK):
        raise SystemExit(f"not executable: {args.switchyard_server}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    upstream = CaptureServer()
    upstream_port = int(upstream.server_address[1])
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    switchyard_port = free_port()

    with tempfile.TemporaryDirectory(prefix="kairo-069-") as temp_dir:
        config = Path(temp_dir) / "switchyard.toml"
        config.write_text(
            "schema_version = 1\n"
            "[llm_clients.openai]\n"
            "format = \"openai_chat\"\n"
            f"base_url = \"http://127.0.0.1:{upstream_port}/v1\"\n"
            "max_retries = 0\n"
            "[targets.gpt]\n"
            f"id = \"{MODEL}\"\n"
            "llm_client = \"openai\"\n"
            "[routes.main]\n"
            "id = \"main\"\n"
            "type = \"passthrough\"\n"
            "target = \"gpt\"\n"
        )
        process = subprocess.Popen(
            [
                args.switchyard_server,
                "--config",
                str(config),
                "--host",
                "127.0.0.1",
                "--port",
                str(switchyard_port),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            wait_for_port(switchyard_port)
            scenarios = [
                ("responses-buffered", "/v1/responses", False),
                ("responses-stream", "/v1/responses", True),
                ("chat-buffered-control", "/v1/chat/completions", False),
                ("chat-stream-control", "/v1/chat/completions", True),
            ]
            summaries: dict[str, dict[str, int]] = {}
            for name, path, streaming in scenarios:
                output = args.output_dir / f"switchyard-{args.label}-{name}.jsonl"
                records = []
                for trial in range(1, args.runs + 1):
                    before = len(upstream.exchanges)
                    request = request_for(path, streaming)
                    status, content_type, client_raw = post(switchyard_port, path, request)
                    if len(upstream.exchanges) != before + 1:
                        raise AssertionError("expected exactly one upstream exchange")
                    exchange = upstream.exchanges[-1]
                    consumer = (
                        responses_consumer(client_raw, streaming)
                        if path == "/v1/responses"
                        else chat_consumer(client_raw, streaming)
                    )
                    record = {
                        "target": {
                            "repository": "https://github.com/NVIDIA-NeMo/Switchyard",
                            "commit": args.commit,
                            "binary_version": "0.2.0",
                            "configuration": "openai_chat backend, passthrough route, max_retries=0",
                        },
                        "trial": trial,
                        "scenario": name,
                        "client_request": {
                            "method": "POST",
                            "path": path,
                            "content_type": "application/json",
                            "body_raw": json.dumps(request, separators=(",", ":")),
                        },
                        "upstream_exchange": exchange,
                        "client_response": {
                            "status": status,
                            "content_type": content_type,
                            "body_raw": client_raw,
                        },
                        "consumer": consumer,
                    }
                    if status != 200:
                        raise AssertionError(f"{name} trial {trial}: HTTP {status}")
                    if REFUSAL not in exchange["response_body_raw"]:
                        raise AssertionError(f"{name} trial {trial}: upstream refusal absent")
                    records.append(record)

                output.write_text("".join(json.dumps(record) + "\n" for record in records))
                classified = sum(
                    1 for record in records if record["consumer"]["classified_as_refusal"]
                )
                if "control" in name and classified != args.runs:
                    raise AssertionError(f"{name}: control preserved {classified}/{args.runs}")
                if "responses" in name and classified != 0:
                    raise AssertionError(f"{name}: typed refusal unexpectedly preserved")
                summaries[name] = {
                    "runs": args.runs,
                    "typed_refusal_detected": classified,
                    "typed_refusal_missed": args.runs - classified,
                }

            summary_path = args.output_dir / f"switchyard-{args.label}-summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "target_commit": args.commit,
                        "label": args.label,
                        "model": MODEL,
                        "results": summaries,
                    },
                    indent=2,
                )
                + "\n"
            )
            print(summary_path.read_text(), end="")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            upstream.shutdown()
            if process.returncode not in (None, 0, -15):
                stderr = process.stderr.read() if process.stderr else ""
                raise RuntimeError(f"switchyard-server exited {process.returncode}: {stderr}")


if __name__ == "__main__":
    main()
