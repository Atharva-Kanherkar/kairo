#!/usr/bin/env python3
"""Leak hunt: what admin secrets a proxy returns to the caller.

Never writes real .env values. Reports FULL / PREFIX_SUFFIX / CANARY hits.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path("transcripts/024")
LITELLM = "http://127.0.0.1:4000"
LIVE = "http://127.0.0.1:4001"
SY = "http://127.0.0.1:9000"
N = 5

CANARIES = [
    "CANARY_QUERY_KEY_IN_BASE",
    "CANARY_DEPLOYMENT_API_KEY",
    "CANARY_EXTRA_HEADERS_AUTHORIZATION",
    "CANARY_X_GOOG_API_KEY_VALUE",
    "CANARY_UNMASKED_HEADER_VALUE",
    "CANARY_AZURE_STYLE_API_KEY",
    "CANARY_HEADERS_FIELD_VALUE",
    "CANARY_OPENAI_ORG_ID",
    "CANARY_AWS_SESSION_TOKEN_VALUE",
    "CANARY_QUERY_KEY",
    "CANARY_SWITCHYARD_BEARER",
]


def load_env_secrets() -> dict[str, str]:
    secrets: dict[str, str] = {}
    env_path = Path(".env")
    if not env_path.exists():
        return secrets
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip().strip("'").strip('"')
        if k.endswith("_API_KEY") or k.endswith("_KEY") or k.endswith("_TOKEN"):
            if v:
                secrets[k] = v
    for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        if os.environ.get(k):
            secrets[k] = os.environ[k]
    return secrets


def redact(body: str, secrets: dict[str, str]) -> str:
    out = body
    for name, val in secrets.items():
        if val:
            out = out.replace(val, f"REDACTED_{name}")
    return out


def scan(body: str, secrets: dict[str, str]) -> list[str]:
    hits: list[str] = []
    for name, val in secrets.items():
        if not val or len(val) < 8:
            continue
        if val in body:
            hits.append(f"FULL:{name}")
        elif val[:4] in body and val[-4:] in body:
            hits.append(f"PREFIX_SUFFIX:{name}")
    for c in CANARIES:
        if c in body:
            hits.append(f"CANARY:{c}")
    return hits


def request(method: str, url: str, payload: dict | None = None, headers: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    hdrs = {"content-type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = resp.read().decode("utf-8", "replace")
            return {"ok": True, "status": resp.status, "body": body}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return {"ok": False, "status": e.code, "body": body}
    except Exception as e:
        return {"ok": False, "status": None, "body": f"{type(e).__name__}: {e}"}


def summarize(tag: str, resp: dict, secrets: dict[str, str]) -> dict:
    body = resp.get("body") or ""
    hits = scan(body, secrets)
    snippet = redact(body, secrets)
    if len(snippet) > 4000:
        snippet = snippet[:4000] + "...truncated..."
    return {
        "tag": tag,
        "status": resp.get("status"),
        "ok": resp.get("ok"),
        "hits": hits,
        "body": snippet,
    }


def main() -> None:
    secrets = load_env_secrets()
    secret_names = sorted(secrets)
    results: list[dict] = []

    litellm_gets = [
        ("ll_models", LITELLM + "/v1/models"),
        ("ll_model_info", LITELLM + "/model/info"),
        ("ll_v1_model_info", LITELLM + "/v1/model/info"),
        ("ll_v2_model_info", LITELLM + "/v2/model/info"),
        ("ll_health", LITELLM + "/health"),
        ("ll_health_live", LITELLM + "/health/liveliness"),
        ("ll_health_ready", LITELLM + "/health/readiness"),
        ("ll_credentials", LITELLM + "/credentials"),
    ]

    for run in range(1, N + 1):
        for tag, url in litellm_gets:
            results.append(summarize(f"{tag} r{run}", request("GET", url), secrets))

        results.append(
            summarize(
                f"ll_chat_plain r{run}",
                request(
                    "POST",
                    LITELLM + "/v1/chat/completions",
                    {"model": "mock", "messages": [{"role": "user", "content": f"hi {run}"}], "max_tokens": 8},
                ),
                secrets,
            )
        )
        results.append(
            summarize(
                f"ll_chat_bad_model r{run}",
                request(
                    "POST",
                    LITELLM + "/v1/chat/completions",
                    {
                        "model": "does-not-exist",
                        "messages": [{"role": "user", "content": f"hi {run}"}],
                        "max_tokens": 8,
                    },
                ),
                secrets,
            )
        )

        results.append(summarize(f"sy_stats r{run}", request("GET", SY + "/v1/stats"), secrets))
        results.append(summarize(f"sy_metrics r{run}", request("GET", SY + "/metrics"), secrets))
        results.append(
            summarize(
                f"sy_chat_transport r{run}",
                request(
                    "POST",
                    SY + "/v1/chat/completions",
                    {
                        "model": "captured-model",
                        "messages": [{"role": "user", "content": f"hi {run}"}],
                        "max_tokens": 8,
                    },
                ),
                secrets,
            )
        )

        # Live LiteLLM: only if up. Scan for real keys, never store them.
        live = request("GET", LIVE + "/health/liveliness")
        if live.get("ok"):
            for tag, path in [
                ("live_models", "/v1/models"),
                ("live_model_info", "/model/info"),
            ]:
                results.append(summarize(f"{tag} r{run}", request("GET", LIVE + path), secrets))

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "hunt-results.json").write_text(json.dumps(results, indent=2) + "\n")

    # Compact scoreboard: tag -> unique hits across repeats
    by_tag: dict[str, dict] = {}
    for row in results:
        base = row["tag"].rsplit(" r", 1)[0]
        slot = by_tag.setdefault(base, {"n": 0, "hits": set(), "statuses": set()})
        slot["n"] += 1
        slot["hits"].update(row["hits"])
        slot["statuses"].add(row["status"])

    print("secret_names_loaded", secret_names)
    print("repeat", N)
    for tag, slot in by_tag.items():
        hits = sorted(slot["hits"])
        print(f"{tag}\tn={slot['n']}\tstatus={sorted(slot['statuses'], key=lambda x: (x is None, x))}\thits={hits}")


if __name__ == "__main__":
    main()
