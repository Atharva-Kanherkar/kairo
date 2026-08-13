#!/usr/bin/env python3
"""Live Gemini control vs LiteLLM. Redacts real keys before writing anything."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path("transcripts/020")
LITELLM = "http://127.0.0.1:4001"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
N = 5
INVALID = "CANARY_INVALID_GEMINI_KEY"


def load_env() -> dict[str, str]:
    env = {}
    for line in Path(".env").read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def redact(text: str, secrets: list[str]) -> str:
    out = text
    for i, s in enumerate(secrets):
        if s and s in out:
            out = out.replace(s, f"[REDACTED_{i}]")
    return out


def post(url: str, payload: dict, headers: dict, timeout: float = 40.0) -> dict:
    body = json.dumps(payload).encode()
    hdrs = {"content-type": "application/json"}
    hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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


def get(url: str, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": True, "status": resp.status, "body": resp.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "body": e.read().decode("utf-8", "replace")}
    except Exception as e:
        return {"ok": False, "status": None, "body": f"{type(e).__name__}: {e}"}


def leak_hits(text: str, secrets: list[str]) -> list[str]:
    hits = []
    for i, s in enumerate(secrets):
        if s and s in text:
            hits.append(f"REDACTED_{i}")
        if s and len(s) > 12 and s[:8] in text and s not in text:
            hits.append(f"PREFIX_{i}")
    return hits


def main() -> None:
    env = load_env()
    gemini_key = env.get("GEMINI_API_KEY") or ""
    openai_key = env.get("OPENAI_API_KEY") or ""
    anthropic_key = env.get("ANTHROPIC_API_KEY") or ""
    openrouter_key = env.get("OPENROUTER_API_KEY") or ""
    secrets = [gemini_key, openai_key, anthropic_key, openrouter_key]
    secrets = [s for s in secrets if s]

    results = []

    gemini_body = {"contents": [{"role": "user", "parts": [{"text": "Reply with the single word ping."}]}]}
    chat_body = {
        "model": "gemini-flash",
        "messages": [{"role": "user", "content": "Reply with the single word ping."}],
        "max_tokens": 8,
    }
    bad_chat = {
        "model": "gemini-flash",
        "messages": [{"role": "user", "content": "ping"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "x",
                    "parameters": {"type": "not-a-real-schema-type"},
                },
            }
        ],
    }

    for run in range(1, N + 1):
        direct_ok = post(GEMINI, gemini_body, {"x-goog-api-key": gemini_key})
        direct_bad = post(GEMINI, gemini_body, {"x-goog-api-key": INVALID})
        litellm_ok = post(f"{LITELLM}/v1/chat/completions", chat_body, {})
        litellm_override = post(
            f"{LITELLM}/v1/chat/completions",
            {**chat_body, "api_key": INVALID},
            {},
        )
        litellm_header = post(
            f"{LITELLM}/v1/chat/completions",
            chat_body,
            {"x-goog-api-key": INVALID},
        )
        litellm_err = post(f"{LITELLM}/v1/chat/completions", bad_chat, {})
        direct_err = post(
            GEMINI,
            {
                "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
                "tools": [{"functionDeclarations": [{"name": "x", "parameters": {"type": "not-a-real-schema-type"}}]}],
            },
            {"x-goog-api-key": gemini_key},
        )

        bundle = {
            "run": run,
            "direct_ok": {
                "status": direct_ok["status"],
                "leaks": leak_hits(direct_ok["body"], secrets),
                "body": redact(direct_ok["body"], secrets)[:800],
            },
            "direct_invalid_key": {
                "status": direct_bad["status"],
                "leaks": leak_hits(direct_bad["body"], secrets),
                "body": redact(direct_bad["body"], secrets)[:800],
            },
            "litellm_ok": {
                "status": litellm_ok["status"],
                "leaks": leak_hits(litellm_ok["body"], secrets),
                "body": redact(litellm_ok["body"], secrets)[:800],
            },
            "litellm_api_key_override": {
                "status": litellm_override["status"],
                "leaks": leak_hits(litellm_override["body"], secrets),
                "body": redact(litellm_override["body"], secrets)[:800],
            },
            "litellm_x_goog_header": {
                "status": litellm_header["status"],
                "leaks": leak_hits(litellm_header["body"], secrets),
                "body": redact(litellm_header["body"], secrets)[:800],
            },
            "litellm_bad_schema": {
                "status": litellm_err["status"],
                "leaks": leak_hits(litellm_err["body"], secrets),
                "body": redact(litellm_err["body"], secrets)[:800],
            },
            "direct_bad_schema": {
                "status": direct_err["status"],
                "leaks": leak_hits(direct_err["body"], secrets),
                "body": redact(direct_err["body"], secrets)[:800],
            },
        }
        results.append(bundle)
        print(
            f"run={run} direct_ok={direct_ok['status']} "
            f"litellm_ok={litellm_ok['status']} "
            f"override={litellm_override['status']} "
            f"header={litellm_header['status']} "
            f"ll_err={litellm_err['status']} "
            f"d_err={direct_err['status']}"
        )

    models = get(f"{LITELLM}/v1/models")
    results.append(
        {
            "probe": "models",
            "status": models["status"],
            "leaks": leak_hits(models["body"], secrets),
            "body": redact(models["body"], secrets)[:1500],
        }
    )

    (OUT / "live-results.json").write_text(json.dumps(results, indent=2))
    print("wrote live-results.json")


if __name__ == "__main__":
    main()
