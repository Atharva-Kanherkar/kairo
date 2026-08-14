#!/usr/bin/env python3
"""Switchyard security hunt: proxy vs direct mock, 5 repeats."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path("transcripts/023")
SY = "http://127.0.0.1:9000"
MOCK = "http://127.0.0.1:9999"
N = 5
CANARY_API_KEY = "CANARY_AZURE_API_KEY"
CANARY_ORG = "CANARY_OPENAI_ORG"
CANARY_GOOG = "CANARY_X_GOOG_API_KEY"
CANARY_AUTH = "CANARY_AUTHORIZATION"
CANARY_X_API = "CANARY_X_API_KEY"
CANARY_JSON_KEY = "CANARY_JSON_API_KEY"
CANARY_BASE = "http://127.0.0.1:9/stolen"


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


def get(url: str) -> dict:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"ok": True, "status": resp.status, "body": resp.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "body": e.read().decode("utf-8", "replace")}
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


def main() -> None:
    results = []
    extra_headers = {
        "x-goog-api-key": CANARY_GOOG,
        "Authorization": f"Bearer {CANARY_AUTH}",
    }
    for run in range(1, N + 1):
        probes = [
            ("control_direct", MOCK + "/v1/chat/completions", chat(f"control_direct run={run}"), None),
            ("sy_plain", SY + "/v1/chat/completions", chat(f"sy_plain run={run}"), None),
            (
                "sy_http_api_key",
                SY + "/v1/chat/completions",
                chat(f"sy_http_api_key run={run}"),
                {"api-key": CANARY_API_KEY},
            ),
            (
                "sy_http_openai_org",
                SY + "/v1/chat/completions",
                chat(f"sy_http_openai_org run={run}"),
                {"OpenAI-Organization": CANARY_ORG, "OpenAI-Project": "CANARY_OPENAI_PROJECT"},
            ),
            (
                "sy_http_x_api_key",
                SY + "/v1/chat/completions",
                chat(f"sy_http_x_api_key run={run}"),
                {"x-api-key": CANARY_X_API},
            ),
            (
                "sy_http_authorization",
                SY + "/v1/chat/completions",
                chat(f"sy_http_authorization run={run}"),
                {"Authorization": f"Bearer {CANARY_AUTH}"},
            ),
            (
                "sy_json_extra_headers",
                SY + "/v1/chat/completions",
                chat(f"sy_json_extra_headers run={run}", extra={"extra_headers": extra_headers}),
                None,
            ),
            (
                "sy_json_headers",
                SY + "/v1/chat/completions",
                chat(f"sy_json_headers run={run}", extra={"headers": extra_headers}),
                None,
            ),
            (
                "sy_json_api_key",
                SY + "/v1/chat/completions",
                chat(f"sy_json_api_key run={run}", extra={"api_key": CANARY_JSON_KEY}),
                None,
            ),
            (
                "sy_json_api_base",
                SY + "/v1/chat/completions",
                chat(f"sy_json_api_base run={run}", extra={"api_base": CANARY_BASE, "base_url": CANARY_BASE}),
                None,
            ),
            (
                "sy_json_store",
                SY + "/v1/chat/completions",
                chat(f"sy_json_store run={run}", extra={"store": True, "user": "CANARY_USER_ID"}),
                None,
            ),
        ]
        for name, url, payload, headers in probes:
            results.append({"probe": name, "run": run, "client": post(url, payload, headers)})
        time.sleep(0.05)

    results.append({"probe": "models", "run": 0, "client": get(f"{SY}/v1/models")})
    results.append({"probe": "stats", "run": 0, "client": get(f"{SY}/v1/stats")})
    results.append({"probe": "metrics", "run": 0, "client": get(f"{SY}/metrics")})
    results.append({"probe": "health", "run": 0, "client": get(f"{SY}/health")})
    results.append(
        {
            "probe": "stats_reset",
            "run": 0,
            "client": post(f"{SY}/v1/stats/reset", {}),
        }
    )

    (OUT / "client-results.json").write_text(json.dumps(results, indent=2))
    print(f"wrote {len(results)} client results")


if __name__ == "__main__":
    main()
