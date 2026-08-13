#!/usr/bin/env python3
"""Security-probe hunt: LiteLLM vs direct mock control, 5 repeats each.

Never prints secret values. Canaries are fake tokens, not real keys.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path("transcripts/020")
LITELLM = "http://127.0.0.1:4000"
MOCK = "http://127.0.0.1:9996"
REDIRECT = "http://127.0.0.1:9997"

CANARY_X_GOOG = "CANARY_X_GOOG_API_KEY"
CANARY_X_CUSTOM = "CANARY_X_CUSTOM_SECRET"
CANARY_X_API = "CANARY_X_API_KEY"
CANARY_BODY_KEY = "CANARY_BODY_API_KEY"
CANARY_AUTH = "CANARY_AUTHORIZATION"

N = 5


def post(url: str, payload: dict, headers: dict | None = None, timeout: float = 20.0) -> dict:
    body = json.dumps(payload).encode()
    hdrs = {"content-type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return {
                "ok": True,
                "status": resp.status,
                "headers": dict(resp.headers.items()),
                "body": raw,
            }
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return {"ok": False, "status": e.code, "headers": dict(e.headers.items()), "body": raw}
    except Exception as e:
        return {"ok": False, "status": None, "headers": {}, "body": f"{type(e).__name__}: {e}"}


def get(url: str, headers: dict | None = None, timeout: float = 20.0) -> dict:
    hdrs = headers or {}
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return {"ok": True, "status": resp.status, "body": raw}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return {"ok": False, "status": e.code, "body": raw}
    except Exception as e:
        return {"ok": False, "status": None, "body": f"{type(e).__name__}: {e}"}


def chat(probe: str, run: int, extra: dict | None = None) -> dict:
    payload = {
        "model": "mock",
        "messages": [{"role": "user", "content": f"{probe} run={run}"}],
        "max_tokens": 8,
    }
    if extra:
        payload.update(extra)
    return payload


def main() -> None:
    results: list[dict] = []
    for run in range(1, N + 1):
        # Control: same payload, hit the mock directly. No extra headers.
        results.append(
            {
                "probe": "control_direct_plain",
                "run": run,
                "client": post(f"{MOCK}/v1/chat/completions", chat("control_direct_plain", run)),
            }
        )
        # Control: same extra headers sent straight to the mock (what a client
        # could do if they talked to the backend themselves).
        results.append(
            {
                "probe": "control_direct_headers",
                "run": run,
                "client": post(
                    f"{MOCK}/v1/chat/completions",
                    chat("control_direct_headers", run),
                    headers={
                        "x-goog-api-key": CANARY_X_GOOG,
                        "x-custom-internal-secret": CANARY_X_CUSTOM,
                        "x-api-key": CANARY_X_API,
                        "Authorization": f"Bearer {CANARY_AUTH}",
                    },
                ),
            }
        )
        # LiteLLM: extra x-* headers + dummy Authorization.
        results.append(
            {
                "probe": "litellm_headers",
                "run": run,
                "client": post(
                    f"{LITELLM}/v1/chat/completions",
                    chat("litellm_headers", run),
                    headers={
                        "x-goog-api-key": CANARY_X_GOOG,
                        "x-custom-internal-secret": CANARY_X_CUSTOM,
                        "x-api-key": CANARY_X_API,
                        "Authorization": f"Bearer {CANARY_AUTH}",
                    },
                ),
            }
        )
        # LiteLLM: client-supplied api_base pointing at the redirect mock.
        results.append(
            {
                "probe": "litellm_api_base",
                "run": run,
                "client": post(
                    f"{LITELLM}/v1/chat/completions",
                    chat("litellm_api_base", run, extra={"api_base": f"{REDIRECT}/v1"}),
                ),
            }
        )
        # LiteLLM: client-supplied api_key.
        results.append(
            {
                "probe": "litellm_api_key_body",
                "run": run,
                "client": post(
                    f"{LITELLM}/v1/chat/completions",
                    chat("litellm_api_key_body", run, extra={"api_key": CANARY_BODY_KEY}),
                ),
            }
        )
        # LiteLLM: mock_response should be stripped.
        results.append(
            {
                "probe": "litellm_mock_response",
                "run": run,
                "client": post(
                    f"{LITELLM}/v1/chat/completions",
                    chat(
                        "litellm_mock_response",
                        run,
                        extra={"mock_response": "BYPASS_MOCK_RESPONSE"},
                    ),
                ),
            }
        )
        # LiteLLM: disable guardrails flag.
        results.append(
            {
                "probe": "litellm_disable_guardrails",
                "run": run,
                "client": post(
                    f"{LITELLM}/v1/chat/completions",
                    chat(
                        "litellm_disable_guardrails",
                        run,
                        extra={"disable_global_guardrails": True},
                    ),
                ),
            }
        )
        # LiteLLM: both api_base and api_key together.
        results.append(
            {
                "probe": "litellm_api_base_and_key",
                "run": run,
                "client": post(
                    f"{LITELLM}/v1/chat/completions",
                    chat(
                        "litellm_api_base_and_key",
                        run,
                        extra={
                            "api_base": f"{REDIRECT}/v1",
                            "api_key": CANARY_BODY_KEY,
                        },
                    ),
                ),
            }
        )
        time.sleep(0.15)

    models = get(f"{LITELLM}/v1/models")
    results.append({"probe": "litellm_models", "run": 0, "client": models})

    (OUT / "client-results.json").write_text(json.dumps(results, indent=2))
    print(f"wrote {len(results)} client results")


if __name__ == "__main__":
    main()
