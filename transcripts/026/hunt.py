#!/usr/bin/env python3
"""Attacker-surface hunt: client-injected creds, org hijack, error leaks.

Canaries only. Never prints .env values. Reports FULL/PREFIX/CANARY hits.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path("transcripts/026")
LL = "http://127.0.0.1:4000"
LL_DOWN = "http://127.0.0.1:4001"
SY = "http://127.0.0.1:9000"
N = 5

CANARIES = [
    "CANARY_BODY_EXTRA_HEADERS_AUTH",
    "CANARY_BODY_X_GOOG",
    "CANARY_BODY_AZURE_API_KEY",
    "CANARY_BODY_OPENAI_ORG",
    "CANARY_BODY_DEFAULT_HEADERS_AUTH",
    "CANARY_BODY_HEADERS_AUTH",
    "CANARY_BODY_API_KEY",
    "CANARY_BODY_ORGANIZATION",
    "CANARY_EXTRA_BODY_API_KEY",
    "CANARY_EXTRA_BODY_API_BASE",
    "CANARY_AWS_SESSION_TOKEN",
    "CANARY_DOWN_QUERY_KEY",
    "CANARY_DOWN_API_KEY",
    "CANARY_DOWN_X_GOOG",
    "CANARY_SY_X_GOOG",
    "CANARY_SY_JSON_ORG",
    "CANARY_SY_JSON_API_KEY",
    "CANARY_SY_JSON_EXTRA_HEADERS",
]


def post(url: str, payload: dict, headers: dict | None = None, timeout: float = 25.0) -> dict:
    body = json.dumps(payload).encode()
    hdrs = {"content-type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return {"ok": True, "status": resp.status, "body": raw}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return {"ok": False, "status": e.code, "body": raw}
    except Exception as e:
        return {"ok": False, "status": None, "body": f"{type(e).__name__}: {e}"}


def get(url: str, timeout: float = 15.0) -> dict:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return {"ok": True, "status": resp.status, "body": raw}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return {"ok": False, "status": e.code, "body": raw}
    except Exception as e:
        return {"ok": False, "status": None, "body": f"{type(e).__name__}: {e}"}


def hits(text: str) -> list[str]:
    return [c for c in CANARIES if c in text]


def chat(model: str, probe: str, run: int, extra: dict | None = None) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": f"{probe} run={run}"}],
        "max_tokens": 8,
    }
    if extra:
        payload.update(extra)
    return payload


def main() -> None:
    rows: list[dict] = []
    for run in range(1, N + 1):
        probes = [
            ("ll_control", LL + "/v1/chat/completions", chat("mock", "control", run), None),
            (
                "ll_extra_headers_auth",
                LL + "/v1/chat/completions",
                chat(
                    "mock",
                    "extra_headers_auth",
                    run,
                    {
                        "extra_headers": {
                            "Authorization": "Bearer CANARY_BODY_EXTRA_HEADERS_AUTH",
                        }
                    },
                ),
                None,
            ),
            (
                "ll_extra_headers_goog",
                LL + "/v1/chat/completions",
                chat(
                    "mock",
                    "extra_headers_goog",
                    run,
                    {"extra_headers": {"x-goog-api-key": "CANARY_BODY_X_GOOG"}},
                ),
                None,
            ),
            (
                "ll_extra_headers_azure",
                LL + "/v1/chat/completions",
                chat(
                    "mock",
                    "extra_headers_azure",
                    run,
                    {"extra_headers": {"api-key": "CANARY_BODY_AZURE_API_KEY"}},
                ),
                None,
            ),
            (
                "ll_extra_headers_org",
                LL + "/v1/chat/completions",
                chat(
                    "mock",
                    "extra_headers_org",
                    run,
                    {"extra_headers": {"OpenAI-Organization": "CANARY_BODY_OPENAI_ORG"}},
                ),
                None,
            ),
            (
                "ll_default_headers_auth",
                LL + "/v1/chat/completions",
                chat(
                    "mock",
                    "default_headers",
                    run,
                    {
                        "default_headers": {
                            "Authorization": "Bearer CANARY_BODY_DEFAULT_HEADERS_AUTH",
                        }
                    },
                ),
                None,
            ),
            (
                "ll_headers_auth",
                LL + "/v1/chat/completions",
                chat(
                    "mock",
                    "headers_field",
                    run,
                    {"headers": {"Authorization": "Bearer CANARY_BODY_HEADERS_AUTH"}},
                ),
                None,
            ),
            (
                "ll_organization",
                LL + "/v1/chat/completions",
                chat("mock", "organization", run, {"organization": "CANARY_BODY_ORGANIZATION"}),
                None,
            ),
            (
                "ll_openai_organization",
                LL + "/v1/chat/completions",
                chat(
                    "mock",
                    "openai_organization",
                    run,
                    {"openai_organization": "CANARY_BODY_ORGANIZATION"},
                ),
                None,
            ),
            (
                "ll_api_key_body",
                LL + "/v1/chat/completions",
                chat("mock", "api_key_body", run, {"api_key": "CANARY_BODY_API_KEY"}),
                None,
            ),
            (
                "ll_extra_body_api_key",
                LL + "/v1/chat/completions",
                chat(
                    "mock",
                    "extra_body_api_key",
                    run,
                    {"extra_body": {"api_key": "CANARY_EXTRA_BODY_API_KEY"}},
                ),
                None,
            ),
            (
                "ll_extra_body_api_base",
                LL + "/v1/chat/completions",
                chat(
                    "mock",
                    "extra_body_api_base",
                    run,
                    {"extra_body": {"api_base": "http://127.0.0.1:9/?key=CANARY_EXTRA_BODY_API_BASE"}},
                ),
                None,
            ),
            (
                "ll_aws_session_token",
                LL + "/v1/chat/completions",
                chat(
                    "mock",
                    "aws_session",
                    run,
                    {"aws_session_token": "CANARY_AWS_SESSION_TOKEN"},
                ),
                None,
            ),
            (
                "ll_down_chat",
                LL_DOWN + "/v1/chat/completions",
                chat("down", "down_chat", run),
                None,
            ),
            (
                "sy_control",
                SY + "/v1/chat/completions",
                chat("captured-model", "sy_control", run),
                None,
            ),
            (
                "sy_header_x_goog",
                SY + "/v1/chat/completions",
                chat("captured-model", "sy_x_goog", run),
                {"x-goog-api-key": "CANARY_SY_X_GOOG"},
            ),
            (
                "sy_json_organization",
                SY + "/v1/chat/completions",
                chat(
                    "captured-model",
                    "sy_json_org",
                    run,
                    {"organization": "CANARY_SY_JSON_ORG"},
                ),
                None,
            ),
            (
                "sy_json_api_key",
                SY + "/v1/chat/completions",
                chat(
                    "captured-model",
                    "sy_json_api_key",
                    run,
                    {"api_key": "CANARY_SY_JSON_API_KEY"},
                ),
                None,
            ),
            (
                "sy_json_extra_headers",
                SY + "/v1/chat/completions",
                chat(
                    "captured-model",
                    "sy_json_eh",
                    run,
                    {
                        "extra_headers": {
                            "Authorization": "Bearer CANARY_SY_JSON_EXTRA_HEADERS"
                        }
                    },
                ),
                None,
            ),
        ]
        for name, url, payload, headers in probes:
            client = post(url, payload, headers=headers)
            rows.append(
                {
                    "probe": name,
                    "run": run,
                    "status": client.get("status"),
                    "client_hits": hits(client.get("body") or ""),
                    "client_body_prefix": (client.get("body") or "")[:500],
                }
            )

        for name, url in [
            ("ll_down_health", LL_DOWN + "/health"),
            ("ll_down_model_info", LL_DOWN + "/model/info"),
            ("ll_down_models", LL_DOWN + "/v1/models"),
            ("ll_down_liveliness", LL_DOWN + "/health/liveliness"),
            ("ll_credentials", LL + "/credentials"),
            ("sy_health", SY + "/health"),
            ("sy_models", SY + "/v1/models"),
            ("sy_stats", SY + "/v1/stats"),
        ]:
            client = get(url)
            rows.append(
                {
                    "probe": name,
                    "run": run,
                    "status": client.get("status"),
                    "client_hits": hits(client.get("body") or ""),
                    "client_body_prefix": (client.get("body") or "")[:500],
                }
            )

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "client-results.json").write_text(json.dumps(rows, indent=2))

    summary: dict[str, dict] = {}
    for row in rows:
        bucket = summary.setdefault(
            row["probe"],
            {"n": 0, "statuses": {}, "hit_runs": 0, "hits": set()},
        )
        bucket["n"] += 1
        st = str(row["status"])
        bucket["statuses"][st] = bucket["statuses"].get(st, 0) + 1
        if row["client_hits"]:
            bucket["hit_runs"] += 1
            bucket["hits"].update(row["client_hits"])

    printable = {
        k: {
            "n": v["n"],
            "statuses": v["statuses"],
            "hit_runs": v["hit_runs"],
            "hits": sorted(v["hits"]),
        }
        for k, v in summary.items()
    }
    (OUT / "scoreboard.json").write_text(json.dumps(printable, indent=2))
    print(json.dumps(printable, indent=2))


if __name__ == "__main__":
    main()
