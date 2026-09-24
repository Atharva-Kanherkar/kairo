"""Allowlist CONNECT proxy for agent containers.

Agent containers sit on an internal Docker network with no route out. This
proxy is the only bridge: it accepts ``CONNECT host:443`` for hosts matching
``KAIRO_EGRESS_ALLOW`` (comma-separated; ``.example.com`` matches subdomains)
and refuses everything else, so an agent can reach its model API but cannot
fetch the upstream fix from GitHub or a newer package from PyPI. Plain HTTP
proxying is refused. Each decision is logged to stdout as one JSON line.

Stdlib only. Runs as ``python egress_proxy.py`` inside a small container.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

ALLOW = [h.strip().lower() for h in os.environ.get("KAIRO_EGRESS_ALLOW", "").split(",") if h.strip()]
PORT = int(os.environ.get("KAIRO_EGRESS_PORT", "3128"))
ALLOWED_PORTS = {443}


def allowed(host: str) -> bool:
    host = host.lower().rstrip(".")
    for rule in ALLOW:
        if rule.startswith("."):
            if host == rule[1:] or host.endswith(rule):
                return True
        elif host == rule:
            return True
    return False


def log(**fields) -> None:
    fields["ts"] = round(time.time(), 3)
    sys.stdout.write(json.dumps(fields) + "\n")
    sys.stdout.flush()


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
    except Exception:
        writer.close()
        return
    line = head.split(b"\r\n", 1)[0].decode("latin-1")
    parts = line.split()
    if len(parts) < 2 or parts[0].upper() != "CONNECT":
        log(decision="deny", reason="non-connect", request=line[:200], peer=str(peer))
        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        return
    host, _, port_s = parts[1].rpartition(":")
    host = host.strip("[]")
    port = int(port_s) if port_s.isdigit() else 0
    if port not in ALLOWED_PORTS or not allowed(host):
        log(decision="deny", host=host, port=port, peer=str(peer))
        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        return
    try:
        up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=30)
    except Exception as exc:
        log(decision="error", host=host, port=port, error=type(exc).__name__)
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        return
    log(decision="allow", host=host, port=port, peer=str(peer))
    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await writer.drain()
    await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))


async def main() -> None:
    server = await asyncio.start_server(handle, "0.0.0.0", PORT)
    log(decision="start", port=PORT, allow=ALLOW)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
