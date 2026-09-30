#!/usr/bin/env python3
"""Minimal raw WebSocket echo server with sanitized wire capture."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import socket
from pathlib import Path


GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def recv_until(connection: socket.socket, marker: bytes) -> bytes:
    data = bytearray()
    while marker not in data:
        chunk = connection.recv(4096)
        if not chunk:
            raise EOFError("connection closed before HTTP headers completed")
        data.extend(chunk)
    return bytes(data)


def sanitize_request(raw: bytes) -> bytes:
    lines = raw.decode("iso-8859-1").split("\r\n")
    sanitized = []
    for line in lines:
        if line.lower().startswith("authorization:"):
            sanitized.append("Authorization: Bearer [REDACTED]")
        else:
            sanitized.append(line)
    return "\r\n".join(sanitized).encode("iso-8859-1")


def header_value(raw: bytes, name: str) -> str:
    prefix = f"{name.lower()}:"
    for line in raw.decode("iso-8859-1").split("\r\n"):
        if line.lower().startswith(prefix):
            return line.split(":", 1)[1].strip()
    raise ValueError(f"missing {name} header")


def recv_exact(connection: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = connection.recv(length - len(data))
        if not chunk:
            raise EOFError("connection closed during WebSocket frame")
        data.extend(chunk)
    return bytes(data)


def read_frame(connection: socket.socket) -> tuple[bytes, int, bytes]:
    first = recv_exact(connection, 2)
    opcode = first[0] & 0x0F
    masked = bool(first[1] & 0x80)
    payload_length = first[1] & 0x7F
    extended = b""
    if payload_length == 126:
        extended = recv_exact(connection, 2)
        payload_length = int.from_bytes(extended, "big")
    elif payload_length == 127:
        extended = recv_exact(connection, 8)
        payload_length = int.from_bytes(extended, "big")
    mask = recv_exact(connection, 4) if masked else b""
    encoded = recv_exact(connection, payload_length)
    decoded = bytes(
        byte ^ mask[index % 4] for index, byte in enumerate(encoded)
    ) if masked else encoded
    return first + extended + mask + encoded, opcode, decoded


def response_frame(opcode: int, payload: bytes) -> bytes:
    if len(payload) >= 126:
        raise ValueError("test payload is unexpectedly large")
    return bytes([0x80 | opcode, len(payload)]) + payload


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def handle(connection: socket.socket, run_dir: Path) -> None:
    request = recv_until(connection, b"\r\n\r\n")
    request_head, overflow = request.split(b"\r\n\r\n", 1)
    request = request_head + b"\r\n\r\n"
    authorization = header_value(request, "Authorization")
    if not authorization.startswith("Bearer "):
        raise ValueError("expected a bearer authorization header")
    key = header_value(request, "Sec-WebSocket-Key")
    accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    ).encode("ascii")
    connection.sendall(response)

    run_dir.mkdir(parents=True)
    (run_dir / "upstream-request.http").write_bytes(sanitize_request(request))
    (run_dir / "upstream-response.http").write_bytes(response)
    if overflow:
        raise ValueError("unexpected bytes after the upgrade request")

    try:
        raw_frame, opcode, payload = read_frame(connection)
    except (EOFError, TimeoutError, socket.timeout):
        write_json(
            run_dir / "result.json",
            {
                "authorization_present": True,
                "authorization_redacted": True,
                "outcome": "no-upstream-frame",
            },
        )
        return
    (run_dir / "upstream-frame.bin").write_bytes(raw_frame)
    (run_dir / "upstream-payload.bin").write_bytes(payload)
    write_json(
        run_dir / "result.json",
        {
            "authorization_present": True,
            "authorization_redacted": True,
            "outcome": "binary-forwarded" if opcode == 0x2 else "control-frame",
            "opcode": opcode,
            "payload_hex": payload.hex(),
            "raw_frame_hex": raw_frame.hex(),
        },
    )

    if opcode == 0x2:
        echoed = response_frame(opcode, payload)
        connection.sendall(echoed)
        (run_dir / "server-frame.bin").write_bytes(echoed)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=19100)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--connections", type=int, default=1)
    parser.add_argument("--ready-file", type=Path)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with socket.create_server((args.host, args.port), reuse_port=False) as server:
        server.settimeout(30)
        if args.ready_file is not None:
            args.ready_file.write_text("ready\n")
        for index in range(1, args.connections + 1):
            connection, _ = server.accept()
            with connection:
                connection.settimeout(10)
                handle(connection, args.output_dir / f"connection-{index:03d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
