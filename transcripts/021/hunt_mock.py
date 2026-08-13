#!/usr/bin/env python3
"""switchyard security hunt: proxy vs direct mock, 5 repeats. canaries only."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path("transcripts/021")
SY = "http://127.0.0.1:9000"
MOCK = "http://127.0.0.1:9999"
N = 5

CANARY_GOOG = "CANARY_X_GOOG_API_KEY"
CANARY_CUSTOM = "CANARY_X_CUSTOM_SECRET"
CANARY_XAPI = "CANARY_X_API_KEY"
CANARY_AUTH = "CANARY_AUTHORIZATION"
CANARY_COOKIE = "CANARY_COOKIE"
CANARY_BODY = "CANARY_BODY_API_KEY"


def post(url: str, payload: dict, headers: dict | None = None) -> dict:
    body = json.dumps(payload).encode()
    hdrs = {"content-type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return {
                "ok": True,
                "status": resp.status,
                "body": resp.read().decode("utf-8", "replace"),
            }
    except urllib.error.HTTPError as e:
        return {
            "ok": False,
            "status": e.code,
            "body": e.read().decode("utf-8", "replace"),
        }
    except Exception as e:
        return {"ok": False, "status": None, "body": f"{type(e).__name__}: {e}"}


def chat(tag: str, extra: dict | None = None) -> dict:
    payload = {
        "model": "captured-model",
        "messages": [{"role": "user", "content": tag}],
        "max_tokens": 8,
    }
    if extra:
        payload.update(extra)
    return payload


def anth(tag: str) -> dict:
    return {
        "model": "captured-model",
        "max_tokens": 8,
        "messages": [{"role": "user", "content": tag}],
    }


def main() -> None:
    results = []
    secret_headers = {
        "x-goog-api-key": CANARY_GOOG,
        "x-custom-internal-secret": CANARY_CUSTOM,
        "x-api-key": CANARY_XAPI,
        "Authorization": f"Bearer {CANARY_AUTH}",
        "Cookie": CANARY_COOKIE,
    }
    for run in range(1, N + 1):
        results.append(
            {
                "probe": "control_direct_plain",
                "run": run,
                "client": post(f"{MOCK}/v1/chat/completions", chat(f"control_direct_plain run={run}")),
            }
        )
        results.append(
            {
                "probe": "control_direct_headers",
                "run": run,
                "client": post(
                    f"{MOCK}/v1/chat/completions",
                    chat(f"control_direct_headers run={run}"),
                    secret_headers,
                ),
            }
        )
        results.append(
            {
                "probe": "switchyard_headers",
                "run": run,
                "client": post(
                    f"{SY}/v1/chat/completions",
                    chat(f"switchyard_headers run={run}"),
                    secret_headers,
                ),
            }
        )
        results.append(
            {
                "probe": "switchyard_api_key_body",
                "run": run,
                "client": post(
                    f"{SY}/v1/chat/completions",
                    chat(f"switchyard_api_key_body run={run}", extra={"api_key": CANARY_BODY}),
                ),
            }
        )
        results.append(
            {
                "probe": "switchyard_anthropic_headers",
                "run": run,
                "client": post(
                    f"{SY}/v1/messages",
                    anth(f"switchyard_anthropic_headers run={run}"),
                    {**secret_headers, "anthropic-version": "2023-06-01"},
                ),
            }
        )
        time.sleep(0.1)

    (OUT / "client-results.json").write_text(json.dumps(results, indent=2))
    print(f"wrote {len(results)} client results")


if __name__ == "__main__":
    main()
