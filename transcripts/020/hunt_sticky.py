#!/usr/bin/env python3
"""Isolated sticky-credential test + extra_body.api_key.

Pair: request WITH client api_key, then request WITHOUT.
If the second request still sends the canary upstream, the key stuck.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path("transcripts/020")
URL = "http://127.0.0.1:4000/v1/chat/completions"
CANARY = "CANARY_STICKY_API_KEY"
N = 5


def post(payload: dict) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        URL, data=body, headers={"content-type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return {"status": resp.status, "body": resp.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": e.read().decode("utf-8", "replace")}


def chat(tag: str, extra: dict | None = None) -> dict:
    payload = {
        "model": "mock",
        "messages": [{"role": "user", "content": tag}],
        "max_tokens": 8,
    }
    if extra:
        payload.update(extra)
    return payload


def main() -> None:
    results = []
    for run in range(1, N + 1):
        with_key = post(
            chat(f"sticky_with run={run}", extra={"api_key": f"{CANARY}_r{run}"})
        )
        time.sleep(0.2)
        without = post(chat(f"sticky_without run={run}"))
        results.append({"run": run, "with": with_key, "without": without})
        time.sleep(0.2)

    for run in range(1, N + 1):
        extra_body = post(
            chat(
                f"extra_body_api_key run={run}",
                extra={"extra_body": {"api_key": "CANARY_EXTRA_BODY_KEY"}},
            )
        )
        results.append({"run": run, "probe": "extra_body_api_key", "client": extra_body})

    (OUT / "sticky-client.json").write_text(json.dumps(results, indent=2))
    print(f"wrote {len(results)} sticky/extra_body results")


if __name__ == "__main__":
    main()
