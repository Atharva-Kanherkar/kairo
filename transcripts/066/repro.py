#!/usr/bin/env python3
"""Reproduce Switchyard dropping Anthropic function-tool strictness without keys."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_PORT = 9994
PROXY_PORT = 9014


def wait_for_port(port: int) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.25)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"port {port} did not open")


def post(path: str, body: dict[str, object], headers: dict[str, str]) -> int:
    request = urllib.request.Request(
        f"http://127.0.0.1:{PROXY_PORT}{path}",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()
            return response.status
    except urllib.error.HTTPError as error:
        error.read()
        return error.code


def schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
        "additionalProperties": False,
    }


def anthropic_request() -> dict[str, object]:
    return {
        "model": "captured-model",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "Use the tool."}],
        "tools": [
            {
                "name": "strict_probe",
                "description": "Verify strictness survives translation.",
                "strict": True,
                "input_schema": schema(),
            }
        ],
    }


def openai_request() -> dict[str, object]:
    return {
        "model": "captured-model",
        "max_completion_tokens": 64,
        "messages": [{"role": "user", "content": "Use the tool."}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "strict_probe",
                    "description": "Verify strictness survives direct OpenAI ingress.",
                    "strict": True,
                    "parameters": schema(),
                },
            }
        ],
    }


def stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=5)


def main() -> None:
    server = os.environ.get("SWITCHYARD_SERVER") or shutil.which("switchyard-server")
    if not server:
        raise SystemExit("set SWITCHYARD_SERVER or put switchyard-server on PATH")
    with tempfile.TemporaryDirectory(prefix="kairo-066-") as temp_dir:
        temp = Path(temp_dir)
        capture = temp / "upstream.jsonl"
        canned = temp / "canned.json"
        config = temp / "switchyard.toml"
        canned.write_text(
            json.dumps(
                {
                    "id": "chatcmpl-kairo-066",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "captured-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                }
            )
        )
        config.write_text(
            "schema_version = 1\n"
            "[llm_clients.local]\n"
            "format = \"openai_chat\"\n"
            f"base_url = \"http://127.0.0.1:{UPSTREAM_PORT}/v1\"\n"
            "[targets.cap]\n"
            "id = \"captured-model\"\n"
            "llm_client = \"local\"\n"
            "[routes.primary]\n"
            "id = \"captured-model\"\n"
            "type = \"passthrough\"\n"
            "target = \"cap\"\n"
        )
        upstream = subprocess.Popen(
            [sys.executable, str(ROOT / "tools/mock_upstream.py"), str(UPSTREAM_PORT), str(capture), str(canned)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proxy: subprocess.Popen[bytes] | None = None
        try:
            wait_for_port(UPSTREAM_PORT)
            proxy = subprocess.Popen(
                [server, "--config", str(config), "--host", "127.0.0.1", "--port", str(PROXY_PORT)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            wait_for_port(PROXY_PORT)
            anthropic_statuses = [
                post("/v1/messages", anthropic_request(), {"anthropic-version": "2023-06-01"})
                for _ in range(5)
            ]
            openai_statuses = [post("/v1/chat/completions", openai_request(), {}) for _ in range(5)]
            records = [json.loads(line) for line in capture.read_text().splitlines()]
            if anthropic_statuses != [200] * 5 or openai_statuses != [200] * 5:
                raise RuntimeError(f"unexpected statuses: {anthropic_statuses}, {openai_statuses}")
            if len(records) != 10:
                raise RuntimeError(f"expected 10 upstream records, got {len(records)}")
            anthro_strict = [row["body"]["tools"][0]["function"].get("strict") for row in records[:5]]
            openai_strict = [row["body"]["tools"][0]["function"].get("strict") for row in records[5:]]
            if anthro_strict != [None] * 5 or openai_strict != [True] * 5:
                raise RuntimeError(f"unexpected strict values: {anthro_strict}, {openai_strict}")
            print(json.dumps({"anthropic_statuses": anthropic_statuses, "openai_statuses": openai_statuses, "captures": records}))
        finally:
            if proxy is not None:
                stop(proxy)
            stop(upstream)


if __name__ == "__main__":
    main()
