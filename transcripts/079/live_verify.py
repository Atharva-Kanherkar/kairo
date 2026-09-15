#!/usr/bin/env python3
"""Confirm issue 079 against OpenAI Realtime without persisting the API key."""

import argparse
import base64
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time


HOST = "127.0.0.1"
IMAGE_DIGEST = "sha256:9e65eb4d0b292c25aaf46d194c705344f19c833a29e30155038bbe551eac7245"
IMAGE = f"maximhq/bifrost@{IMAGE_DIGEST}"
VALID_VK = "sk-bf-valid-synthetic-live-realtime-079"
FORGED_VK = "sk-bf-forged-does-not-exist-live-realtime-079"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def free_port():
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def open_websocket(port, virtual_key=None):
    key = base64.b64encode(os.urandom(16)).decode()
    lines = [
        "GET /v1/realtime?model=openai/gpt-realtime HTTP/1.1",
        f"Host: {HOST}:{port}",
        "Connection: Upgrade",
        "Upgrade: websocket",
        "Sec-WebSocket-Version: 13",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Protocol: realtime",
    ]
    if virtual_key is not None:
        lines.append(f"x-bf-vk: {virtual_key}")
    sock = socket.create_connection((HOST, port), timeout=10)
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response += chunk
    header, _, remainder = response.partition(b"\r\n\r\n")
    status = int(header.split(b"\r\n", 1)[0].split(b" ", 2)[1])
    return sock, status, remainder


def read_text_frame(sock, initial=b""):
    data = bytearray(initial)
    sock.settimeout(20)
    while len(data) < 2:
        data.extend(sock.recv(4096))
    length = data[1] & 0x7F
    offset = 2
    if length == 126:
        while len(data) < 4:
            data.extend(sock.recv(4096))
        length = int.from_bytes(data[2:4], "big")
        offset = 4
    elif length == 127:
        while len(data) < 10:
            data.extend(sock.recv(4096))
        length = int.from_bytes(data[2:10], "big")
        offset = 10
    while len(data) < offset + length:
        data.extend(sock.recv(4096))
    require((data[0] & 0x0F) == 1, "expected text WebSocket frame")
    return json.loads(bytes(data[offset:offset + length]))


def wait_ready(port, process):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        require(process.poll() is None, "Bifrost exited before readiness")
        try:
            conn = http.client.HTTPConnection(HOST, port, timeout=2)
            conn.request("GET", "/api/version")
            response = conn.getresponse()
            body = response.read()
            conn.close()
            if response.status == 200:
                return json.loads(body)
        except (OSError, http.client.HTTPException, json.JSONDecodeError):
            pass
        time.sleep(0.25)
    raise RuntimeError("Bifrost readiness timeout")


def config():
    return {
        "$schema": "https://www.getbifrost.ai/schema",
        "client": {"enable_logging": False, "enforce_auth_on_inference": True},
        "config_store": {
            "enabled": True,
            "type": "sqlite",
            "config": {"path": "/app/data/config.db"},
        },
        "logs_store": {"enabled": False},
        "governance": {
            "virtual_keys": [{
                "id": "vk-valid-live-realtime-079",
                "name": "valid-live-realtime-control",
                "value": VALID_VK,
                "is_active": True,
                "provider_configs": [{
                    "provider": "openai",
                    "allowed_models": ["*"],
                    "key_ids": ["openai-live-key"],
                    "weight": 1,
                }],
            }],
        },
        "providers": {
            "openai": {
                "keys": [{
                    "id": "openai-live-key",
                    "name": "openai-live-key",
                    "value": "env.OPENAI_API_KEY",
                    "weight": 1,
                    "models": ["*"],
                }],
            },
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()
    require(os.environ.get("OPENAI_API_KEY", "").startswith("sk-"),
            "OPENAI_API_KEY is missing")

    port = free_port()
    run_dir = Path(tempfile.mkdtemp(prefix=".bifrost-live-079-", dir="/home/atharva"))
    app_dir = run_dir / "app"
    app_dir.mkdir()
    (app_dir / "config.json").write_text(json.dumps(config(), indent=2))

    process = None
    results = {"version": None, "runs": []}
    try:
        process = subprocess.Popen(
            [
                "docker", "run", "--rm", "--network", "host",
                "-e", "OPENAI_API_KEY", "-v", f"{app_dir}:/app/data",
                IMAGE, "--host", HOST, "--port", str(port),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        results["version"] = wait_ready(port, process)
        absent, absent_status, _ = open_websocket(port)
        absent.close()
        require(absent_status == 401, f"absent control returned {absent_status}")

        for _ in range(args.runs):
            row = {"absent_status": absent_status}
            for name, key in (("forged", FORGED_VK), ("valid", VALID_VK)):
                sock, status, remainder = open_websocket(port, key)
                row[f"{name}_status"] = status
                require(status == 101, f"{name} path returned HTTP {status}")
                event = read_text_frame(sock, remainder)
                sock.close()
                row[f"{name}_first_event_type"] = event.get("type")
                row[f"{name}_error_code"] = event.get("error", {}).get("code")
                require(
                    event.get("type") == "session.created",
                    f"{name} path did not receive OpenAI session.created",
                )
            results["runs"].append(row)

        print(json.dumps(results, indent=2))
        print("VERDICT: forged and valid keys both reached live OpenAI Realtime")
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        shutil.rmtree(run_dir)


if __name__ == "__main__":
    main()
