#!/usr/bin/env python3
"""Reproduce LiteLLM 1.96.2 dropping Anthropic tool strictness without API keys."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parents[2]
LITELLM_PYTHON = ROOT / "tools/litellm-env/bin/python"
LITELLM_CONFIG = ROOT / "tools/litellm-mock.yaml"
RESPONSES_CANNED = ROOT / "transcripts/016/canned-responses.json"
CHAT_CANNED = ROOT / "transcripts/020/canned-openai.json"
UPSTREAM_PORT = 9996
PROXY_PORT = 4000


class CaptureHandler(BaseHTTPRequestHandler):
    """Record upstream JSON and return the response shape required by each path."""

    records: list[dict[str, object]] = []

    def log_message(self, _format: str, *_args: object) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length))
        self.records.append({"path": self.path, "body": body})
        canned = RESPONSES_CANNED if self.path == "/v1/responses" else CHAT_CANNED
        payload = canned.read_bytes()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def wait_for_port(port: int) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.25)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"port {port} did not open")


def post(
    port: int,
    path: str,
    body: dict[str, object],
    headers: dict[str, str],
) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def strict_tool() -> dict[str, object]:
    return {
        "name": "strict_probe",
        "description": "strictness must survive translation",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
            "additionalProperties": False,
        },
    }


def main() -> None:
    if not LITELLM_PYTHON.is_file():
        raise SystemExit(f"missing LiteLLM interpreter: {LITELLM_PYTHON}")
    server = HTTPServer(("127.0.0.1", UPSTREAM_PORT), CaptureHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = os.environ.copy()
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        env.pop(name, None)
    proxy = subprocess.Popen(
        [
            str(LITELLM_PYTHON),
            "-c",
            "from litellm import run_server; run_server()",
            "--config",
            str(LITELLM_CONFIG),
            "--port",
            str(PROXY_PORT),
            "--telemetry",
            "False",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        wait_for_port(PROXY_PORT)
        statuses = []
        messages_request = {
            "model": "mock",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [strict_tool()],
        }
        for _ in range(5):
            status, _ = post(
                PROXY_PORT,
                "/v1/messages",
                messages_request,
                {"anthropic-version": "2023-06-01"},
            )
            statuses.append(status)
        strict_schema = strict_tool()["input_schema"]
        chat_request = {
            "model": "mock",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "strict_probe",
                        "description": "strictness must survive translation",
                        "strict": True,
                        "parameters": strict_schema,
                    },
                }
            ],
        }
        chat_status, _ = post(PROXY_PORT, "/v1/chat/completions", chat_request, {})
        direct_status, _ = post(
            UPSTREAM_PORT,
            "/v1/responses",
            {
                "model": "mockmodel",
                "input": "hi",
                "tools": [
                    {
                        "type": "function",
                        "name": "strict_probe",
                        "strict": True,
                        "parameters": strict_schema,
                    }
                ],
            },
            {},
        )
        upstream = CaptureHandler.records
        expected_paths = ["/v1/responses"] * 5 + ["/v1/chat/completions", "/v1/responses"]
        if [record["path"] for record in upstream] != expected_paths:
            raise RuntimeError(f"unexpected capture paths: {[record['path'] for record in upstream]}")
        messages_tools = [record["body"]["tools"][0] for record in upstream[:5]]
        chat_tool = upstream[5]["body"]["tools"][0]["function"]
        direct_tool = upstream[6]["body"]["tools"][0]
        if statuses != [200] * 5 or chat_status != 200 or direct_status != 200:
            raise RuntimeError(
                f"unexpected client statuses: messages={statuses}, chat={chat_status}, direct={direct_status}"
            )
        if any("strict" in tool for tool in messages_tools):
            raise RuntimeError("strict unexpectedly survived the Anthropic ingress")
        if chat_tool.get("strict") is not True:
            raise RuntimeError("strict did not survive the OpenAI ingress control")
        if direct_tool.get("strict") is not True:
            raise RuntimeError("strict did not survive the direct Responses control")
        print(
            json.dumps(
                {
                    "messages_statuses": statuses,
                    "chat_status": chat_status,
                    "direct_status": direct_status,
                    "captures": upstream,
                }
            )
        )
    finally:
        if proxy.poll() is None:
            os.killpg(proxy.pid, signal.SIGTERM)
            proxy.wait(timeout=5)
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
