#!/usr/bin/env python3
"""Live Gemini control vs LiteLLM extra_headers. Redacts real keys before writing."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path("transcripts/022")
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


def post(url: str, payload: dict, headers: dict | None = None, timeout: float = 40.0) -> dict:
    body = json.dumps(payload).encode()
    hdrs = {"content-type": "application/json"}
    if headers:
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


def leak_hits(text: str, secrets: list[str]) -> list[str]:
    hits = []
    for i, s in enumerate(secrets):
        if s and s in text:
            hits.append(f"REDACTED_{i}")
        if s and len(s) > 12 and s[:8] in text and s not in text:
            hits.append(f"PREFIX_{i}")
    return hits


def slim(resp: dict, secrets: list[str]) -> dict:
    return {
        "status": resp["status"],
        "leaks": leak_hits(resp["body"], secrets),
        "body": redact(resp["body"], secrets)[:800],
    }


def main() -> None:
    env = load_env()
    secrets = [
        env.get("GEMINI_API_KEY") or "",
        env.get("OPENAI_API_KEY") or "",
        env.get("ANTHROPIC_API_KEY") or "",
        env.get("OPENROUTER_API_KEY") or "",
    ]
    secrets = [s for s in secrets if s]
    gemini_key = env.get("GEMINI_API_KEY") or ""

    gemini_body = {
        "contents": [{"role": "user", "parts": [{"text": "Reply with the single word ping."}]}]
    }
    chat_body = {
        "model": "gemini-flash",
        "messages": [{"role": "user", "content": "Reply with the single word ping."}],
        "max_tokens": 8,
    }

    results = []
    for run in range(1, N + 1):
        direct_ok = post(GEMINI, gemini_body, {"x-goog-api-key": gemini_key})
        direct_bad_goog = post(GEMINI, gemini_body, {"x-goog-api-key": INVALID})
        direct_bearer_plus_bad_goog = post(
            GEMINI,
            gemini_body,
            {"Authorization": f"Bearer {gemini_key}", "x-goog-api-key": INVALID},
        )
        litellm_ok = post(f"{LITELLM}/v1/chat/completions", chat_body)
        litellm_extra_goog = post(
            f"{LITELLM}/v1/chat/completions",
            {**chat_body, "extra_headers": {"x-goog-api-key": INVALID}},
        )
        litellm_headers_goog = post(
            f"{LITELLM}/v1/chat/completions",
            {**chat_body, "headers": {"x-goog-api-key": INVALID}},
        )
        litellm_extra_auth = post(
            f"{LITELLM}/v1/chat/completions",
            {**chat_body, "extra_headers": {"Authorization": f"Bearer {INVALID}"}},
        )
        litellm_http_header = post(
            f"{LITELLM}/v1/chat/completions",
            chat_body,
            {"x-goog-api-key": INVALID},
        )
        bundle = {
            "run": run,
            "direct_ok": slim(direct_ok, secrets),
            "direct_invalid_x_goog": slim(direct_bad_goog, secrets),
            "direct_real_bearer_plus_invalid_x_goog": slim(direct_bearer_plus_bad_goog, secrets),
            "litellm_ok": slim(litellm_ok, secrets),
            "litellm_extra_headers_x_goog": slim(litellm_extra_goog, secrets),
            "litellm_headers_field_x_goog": slim(litellm_headers_goog, secrets),
            "litellm_extra_headers_authorization": slim(litellm_extra_auth, secrets),
            "litellm_http_x_goog_header": slim(litellm_http_header, secrets),
        }
        results.append(bundle)
        print(
            f"run={run} direct_ok={direct_ok['status']} "
            f"direct_bad_goog={direct_bad_goog['status']} "
            f"direct_bearer_plus_bad={direct_bearer_plus_bad_goog['status']} "
            f"ll_ok={litellm_ok['status']} "
            f"ll_extra_goog={litellm_extra_goog['status']} "
            f"ll_headers_goog={litellm_headers_goog['status']} "
            f"ll_extra_auth={litellm_extra_auth['status']} "
            f"ll_http_header={litellm_http_header['status']}"
        )

    (OUT / "live-results.json").write_text(json.dumps(results, indent=2))
    print("wrote live-results.json")


if __name__ == "__main__":
    main()
