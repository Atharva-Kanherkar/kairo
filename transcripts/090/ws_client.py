#!/usr/bin/env python3
"""Drive one credentialed binary WebSocket message from inside a sandbox."""

from __future__ import annotations

import argparse
import json
import os

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect


PAYLOAD = b"KAIRO-090-BINARY-ACTION"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://192.168.65.254:19100/ws")
    args = parser.parse_args()

    token = os.environ.get("KAIRO_WS_TOKEN")
    if not token:
        raise SystemExit("KAIRO_WS_TOKEN is missing")

    result: dict[str, object]
    try:
        with connect(
            args.url,
            additional_headers={"Authorization": f"Bearer {token}"},
            open_timeout=5,
            close_timeout=2,
        ) as websocket:
            websocket.send(PAYLOAD)
            received = websocket.recv(timeout=5)
            if not isinstance(received, bytes):
                raise RuntimeError("expected a binary echo")
            result = {
                "outcome": "forwarded",
                "sent_hex": PAYLOAD.hex(),
                "received_hex": received.hex(),
                "byte_equal": received == PAYLOAD,
            }
    except ConnectionClosed as error:
        result = {
            "outcome": "closed",
            "close_code": error.code,
            "close_reason": error.reason,
        }

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
