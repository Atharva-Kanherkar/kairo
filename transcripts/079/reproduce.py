#!/usr/bin/env python3
"""Probe Bifrost Realtime admission with absent, forged, and valid virtual keys."""

import base64
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time


HOST = "127.0.0.1"
IMAGE_DIGEST = "sha256:9e65eb4d0b292c25aaf46d194c705344f19c833a29e30155038bbe551eac7245"
IMAGE = f"maximhq/bifrost@{IMAGE_DIGEST}"
VALID_VK = "sk-bf-valid-synthetic-realtime-079"
FORGED_VK = "sk-bf-forged-does-not-exist-realtime-079"
PROVIDER_KEY = "synthetic-provider-key-realtime-079"
RUNS = 5


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def free_port():
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


class CaptureHandler(socketserver.StreamRequestHandler):
    def handle(self):
        request_line = self.rfile.readline()
        if not request_line:
            return
        headers = {}
        raw_headers = []
        while True:
            line = self.rfile.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            raw_headers.append(line)
            name, _, value = line.partition(b":")
            headers[name.decode("latin-1").lower()] = value.strip().decode("latin-1")
        path = request_line.decode("latin-1").split(" ", 2)[1]

        if headers.get("upgrade", "").lower() == "websocket":
            key = headers.get("sec-websocket-key", "")
            accept = base64.b64encode(
                hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
            ).decode()
            response = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n"
                "Sec-WebSocket-Protocol: realtime\r\n\r\n"
            ).encode()
            self.request.sendall(response)
            with self.server.lock:
                self.server.handshakes.append({
                    "path": path,
                    "authorization": headers.get("authorization", ""),
                    "raw": request_line + b"".join(raw_headers) + b"\r\n",
                })
            self.request.settimeout(10)
            try:
                while self.request.recv(4096):
                    pass
            except (OSError, socket.timeout):
                pass
            return

        body = json.dumps(
            {"object": "list", "data": [{"id": "mock-model", "object": "model"}]},
            separators=(",", ":"),
        ).encode()
        self.request.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )


class CaptureServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, CaptureHandler)
        self.lock = threading.Lock()
        self.handshakes = []


def open_websocket(port, virtual_key=None):
    key = base64.b64encode(os.urandom(16)).decode()
    lines = [
        "GET /v1/realtime?model=mockoai/mock-model HTTP/1.1",
        f"Host: {HOST}:{port}",
        "Connection: Upgrade",
        "Upgrade: websocket",
        "Sec-WebSocket-Version: 13",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Protocol: realtime",
    ]
    if virtual_key is not None:
        lines.append(f"x-bf-vk: {virtual_key}")
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode()
    sock = socket.create_connection((HOST, port), timeout=10)
    sock.sendall(raw)
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response += chunk
    header, _, remainder = response.partition(b"\r\n\r\n")
    status_line = header.split(b"\r\n", 1)[0].decode("latin-1")
    return sock, int(status_line.split(" ", 2)[1]), status_line, remainder, raw, response


def websocket_status(port, virtual_key=None):
    sock, status, status_line, _, _, _ = open_websocket(port, virtual_key)
    sock.close()
    return status, status_line


def ordinary_http_status(port, virtual_key):
    conn = http.client.HTTPConnection(HOST, port, timeout=5)
    conn.request("GET", "/v1/models", headers={"x-bf-vk": virtual_key})
    response = conn.getresponse()
    body = response.read()
    raw_response = (
        f"HTTP/1.1 {response.status} {response.reason}\r\n"
        + "".join(f"{name}: {value}\r\n" for name, value in response.getheaders())
        + "\r\n"
    ).encode("latin-1") + body
    conn.close()
    raw_request = (
        f"GET /v1/models HTTP/1.1\r\nHost: {HOST}:{port}\r\n"
        f"x-bf-vk: {virtual_key}\r\n\r\n"
    ).encode("latin-1")
    return response.status, raw_request, raw_response


def read_server_text_frame(sock, initial=b""):
    data = bytearray(initial)
    sock.settimeout(5)
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
    require((data[0] & 0x0F) == 1, "expected a text WebSocket frame")
    frame = bytes(data[:offset + length])
    return json.loads(frame[offset:]), frame
def wait_ready(port, process, log_path):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        require(process.poll() is None, f"Bifrost exited early; see {log_path}")
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
    raise RuntimeError(f"Bifrost did not become ready; see {log_path}")


def config(upstream_port, app_dir):
    return {
        "$schema": "https://www.getbifrost.ai/schema",
        "client": {
            "enable_logging": False,
            "enforce_auth_on_inference": True,
            "initial_pool_size": 8,
        },
        "config_store": {
            "enabled": True,
            "type": "sqlite",
            "config": {"path": "/app/data/config.db"},
        },
        "logs_store": {"enabled": False},
        "websocket": {"max_connections_per_user": 2},
        "governance": {
            "virtual_keys": [{
                "id": "vk-valid-realtime-079",
                "name": "valid-realtime-control",
                "value": VALID_VK,
                "is_active": True,
                "provider_configs": [{
                    "provider": "mockoai",
                    "allowed_models": ["*"],
                    "key_ids": ["mock-key"],
                    "weight": 1,
                }],
            }],
        },
        "providers": {
            "mockoai": {
                "keys": [{
                    "id": "mock-key",
                    "name": "mock-key",
                    "value": PROVIDER_KEY,
                    "weight": 1,
                    "models": ["*"],
                }],
                "network_config": {
                    "base_url": f"http://{HOST}:{upstream_port}",
                    "allow_private_network": True,
                },
                "custom_provider_config": {"base_provider_type": "openai"},
            }
        },
    }


def sanitized_upstream(raw):
    return raw.replace(
        f"Bearer {PROVIDER_KEY}".encode(),
        b"Bearer <BIFROST_PROVIDER_AUTH>",
    )


def write_exchange(path, request, response):
    path.write_bytes(request + b"\n--- RESPONSE ---\n" + response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    require(
        sys.platform.startswith("linux"),
        "this script requires a Linux host: it runs the gateway with "
        "docker --network host and expects the container to reach the capture "
        "upstream on 127.0.0.1, which Docker Desktop for macOS and Windows do "
        "not provide by default. Use a Linux host or VM.",
    )
    upstream_port = free_port()
    bifrost_port = free_port()
    capture = CaptureServer((HOST, upstream_port))
    capture_thread = threading.Thread(target=capture.serve_forever, daemon=True)
    capture_thread.start()

    run_dir = args.output or Path(
        tempfile.mkdtemp(prefix="bifrost-realtime-079-", dir="/tmp")
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    app_dir = run_dir / "app"
    app_dir.mkdir()
    (app_dir / "config.json").write_text(json.dumps(config(upstream_port, app_dir), indent=2))
    log_path = run_dir / "bifrost.log"

    inspected = subprocess.check_output(
        ["docker", "image", "inspect", IMAGE, "--format", "{{index .RepoDigests 0}}"],
        text=True,
    ).strip()
    require(inspected.endswith("@" + IMAGE_DIGEST), f"unexpected image digest: {inspected}")

    log_file = log_path.open("wb")
    process = subprocess.Popen(
        [
            "docker", "run", "--rm", "--network", "host",
            "-v", f"{app_dir}:/app/data",
            IMAGE, "--host", HOST, "--port", str(bifrost_port),
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    try:
        version = wait_ready(bifrost_port, process, log_path)
        results = {"version": version, "image_digest": IMAGE_DIGEST, "cells": {}}

        ordinary_before = len(capture.handshakes)
        ordinary_status, ordinary_request, ordinary_response = ordinary_http_status(
            bifrost_port, FORGED_VK
        )
        require(ordinary_status == 401, "ordinary HTTP route accepted forged virtual key")
        require(len(capture.handshakes) == ordinary_before,
                "ordinary HTTP forged-key control reached upstream")
        write_exchange(
            run_dir / "control-http-forged-key.http",
            ordinary_request,
            ordinary_response,
        )
        results["ordinary_http_control"] = {
            "status": ordinary_status,
            "upstream_handshakes": len(capture.handshakes) - ordinary_before,
        }

        cells = [
            ("absent", None),
            ("forged", FORGED_VK),
            ("valid", VALID_VK),
        ]
        cell_ranges = {}
        for name, key in cells:
            statuses = []
            before = len(capture.handshakes)
            for iteration in range(RUNS):
                sock, status, line, _, raw_request, raw_response = open_websocket(
                    bifrost_port, key
                )
                sock.close()
                statuses.append({"status": status, "line": line})
                if iteration == 0:
                    write_exchange(
                        run_dir / f"{name}-realtime.http",
                        raw_request,
                        raw_response,
                    )
                time.sleep(0.3)
            after = len(capture.handshakes)
            cell_ranges[name] = (before, after)
            results["cells"][name] = {
                "statuses": statuses,
                "upstream_handshakes": after - before,
            }
            time.sleep(0.5)

        require(all(x["status"] == 401 for x in results["cells"]["absent"]["statuses"]),
                "anonymous control did not fail with 401")
        require(results["cells"]["absent"]["upstream_handshakes"] == 0,
                "anonymous control reached upstream")
        require(all(x["status"] == 101 for x in results["cells"]["valid"]["statuses"]),
                "valid virtual-key control did not upgrade")
        require(results["cells"]["valid"]["upstream_handshakes"] == RUNS,
                "valid virtual-key control did not reach upstream every time")

        baseline_before = len(capture.handshakes)
        baseline, baseline_status, _, baseline_remainder, _, _ = open_websocket(
            bifrost_port, VALID_VK
        )
        require(baseline_status == 101, "valid baseline did not upgrade")
        deadline = time.monotonic() + 5
        while len(capture.handshakes) < baseline_before + 1 and time.monotonic() < deadline:
            time.sleep(0.05)
        require(len(capture.handshakes) == baseline_before + 1,
                "valid baseline did not open an upstream connection")
        require(not baseline_remainder, "valid baseline unexpectedly received an immediate frame")
        baseline.close()
        time.sleep(0.5)

        forged_sockets = []
        impact_before = len(capture.handshakes)
        for _ in range(2):
            forged_sock, forged_status, _, _, _, _ = open_websocket(
                bifrost_port, FORGED_VK
            )
            require(forged_status == 101, "forged connection did not upgrade during impact test")
            forged_sockets.append(forged_sock)
        deadline = time.monotonic() + 5
        while len(capture.handshakes) < impact_before + 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        require(len(capture.handshakes) == impact_before + 2,
                "forged connections did not occupy two upstream sessions")

        valid_blocked, blocked_status, _, blocked_remainder, blocked_request, blocked_response = (
            open_websocket(bifrost_port, VALID_VK)
        )
        require(blocked_status == 101, "saturated valid connection did not complete client upgrade")
        blocked_frame, blocked_frame_raw = read_server_text_frame(
            valid_blocked, blocked_remainder
        )
        valid_blocked.close()
        results["impact"] = {
            "configured_connection_limit": 2,
            "forged_sessions_held": len(forged_sockets),
            "valid_client_http_status": blocked_status,
            "valid_client_first_frame": blocked_frame,
            "valid_client_opened_upstream": len(capture.handshakes) != impact_before + 2,
        }
        write_exchange(
            run_dir / "impact-valid-after-forged-saturation.http",
            blocked_request,
            blocked_response.partition(b"\r\n\r\n")[0] + b"\r\n\r\n",
        )
        (run_dir / "impact-valid-first-frame.json").write_text(
            json.dumps(blocked_frame, indent=2) + "\n"
        )
        (run_dir / "impact-valid-first-frame.hex").write_text(
            blocked_frame_raw.hex() + "\n"
        )
        for forged_sock in forged_sockets:
            forged_sock.close()

        require(blocked_frame.get("error", {}).get("code") == "rate_limit_exceeded",
                f"expected valid user to receive rate_limit_exceeded, got {blocked_frame}")
        require(not results["impact"]["valid_client_opened_upstream"],
                "valid user unexpectedly opened an upstream session after saturation")

        with capture.lock:
            upstream = list(capture.handshakes)
        require(upstream, "no upstream Realtime handshakes were captured")
        require(
            all(
                handshake["authorization"] == f"Bearer {PROVIDER_KEY}"
                for handshake in upstream
            ),
            "an upstream Realtime handshake did not use the configured provider key",
        )
        for index, handshake in enumerate(upstream, 1):
            (run_dir / f"upstream-handshake-{index:02}.http").write_bytes(
                sanitized_upstream(handshake["raw"])
            )

        # Also write the role-tagged names the writeup cites and the conformance
        # fixtures load, so re-running this script regenerates the cited evidence
        # instead of only a sequential set that a reader has to map by hand.
        forged_start, _ = cell_ranges["forged"]
        named = {
            "upstream-forged-handshake.http": upstream[forged_start],
            "upstream-impact-forged-1.http": upstream[impact_before],
            "upstream-impact-forged-2.http": upstream[impact_before + 1],
        }
        for filename, handshake in named.items():
            (run_dir / filename).write_bytes(sanitized_upstream(handshake["raw"]))

        results["upstream_handshakes_total"] = len(upstream)
        results["named_upstream_captures"] = {
            "upstream-forged-handshake.http": forged_start + 1,
            "upstream-impact-forged-1.http": impact_before + 1,
            "upstream-impact-forged-2.http": impact_before + 2,
        }
        (run_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")

        print(json.dumps(results, indent=2))
        print(f"run_dir={run_dir}")
        if all(x["status"] == 101 for x in results["cells"]["forged"]["statuses"]) and \
                results["cells"]["forged"]["upstream_handshakes"] == RUNS:
            print("VERDICT: forged virtual key opened an upstream Realtime connection")
        else:
            print("VERDICT: candidate did not reproduce")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log_file.close()
        capture.shutdown()
        capture.server_close()


if __name__ == "__main__":
    main()
