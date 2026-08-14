#!/usr/bin/env python3
"""litellm extra_headers body hunt: proxy vs direct mock, 5 repeats."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path("transcripts/022")
LITELLM = "http://127.0.0.1:4000"
MOCK = "http://127.0.0.1:9996"
N = 5
CANARY_GOOG = "CANARY_X_GOOG_API_KEY"
CANARY_CUSTOM = "CANARY_X_CUSTOM_SECRET"
CANARY_AUTH = "CANARY_AUTHORIZATION"


def post(url: str, payload: dict, headers: dict | None = None) -> dict:
    body = json.dumps(payload).encode()
    hdrs = {"content-type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return {"ok": True, "status": resp.status, "body": resp.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "body": e.read().decode("utf-8", "replace")}
    except Exception as e:
        return {"ok": False, "status": None, "body": f"{type(e).__name__}: {e}"}


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
    extra = {
        "x-goog-api-key": CANARY_GOOG,
        "x-custom-internal-secret": CANARY_CUSTOM,
    }
    extra_auth = {**extra, "Authorization": f"Bearer {CANARY_AUTH}"}
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
                "probe": "litellm_plain",
                "run": run,
                "client": post(f"{LITELLM}/v1/chat/completions", chat(f"litellm_plain run={run}")),
            }
        )
        results.append(
            {
                "probe": "litellm_extra_headers",
                "run": run,
                "client": post(
                    f"{LITELLM}/v1/chat/completions",
                    chat(f"litellm_extra_headers run={run}", extra={"extra_headers": extra}),
                ),
            }
        )
        results.append(
            {
                "probe": "litellm_extra_headers_auth",
                "run": run,
                "client": post(
                    f"{LITELLM}/v1/chat/completions",
                    chat(
                        f"litellm_extra_headers_auth run={run}",
                        extra={"extra_headers": extra_auth},
                    ),
                ),
            }
        )
        results.append(
            {
                "probe": "litellm_headers_field",
                "run": run,
                "client": post(
                    f"{LITELLM}/v1/chat/completions",
                    chat(f"litellm_headers_field run={run}", extra={"headers": extra}),
                ),
            }
        )
        time.sleep(0.1)
    (OUT / "client-results.json").write_text(json.dumps(results, indent=2))
    print(f"wrote {len(results)} client results")


if __name__ == "__main__":
    main()
