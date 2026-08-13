#!/usr/bin/env python3
"""live gemini vs switchyard. redacts real keys before writing."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path("transcripts/021")
SY = "http://127.0.0.1:9001"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
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


def leak_hits(text: str, secrets: list[str]) -> list[str]:
    hits = []
    for i, s in enumerate(secrets):
        if s and s in text:
            hits.append(f"REDACTED_{i}")
    return hits


def post(url: str, payload: dict, headers: dict, timeout: float = 40.0) -> dict:
    body = json.dumps(payload).encode()
    hdrs = {"content-type": "application/json"}
    hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": True, "status": resp.status, "body": resp.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "body": e.read().decode("utf-8", "replace")}
    except Exception as e:
        return {"ok": False, "status": None, "body": f"{type(e).__name__}: {e}"}


def main() -> None:
    env = load_env()
    gemini_key = env.get("GEMINI_API_KEY") or ""
    secrets = [s for s in [gemini_key, env.get("OPENAI_API_KEY"), env.get("ANTHROPIC_API_KEY"), env.get("OPENROUTER_API_KEY")] if s]
    chat = {
        "model": "gemini-2.5-flash",
        "messages": [{"role": "user", "content": "Reply with the single word ping."}],
        "max_tokens": 8,
    }
    results = []
    for run in range(1, N + 1):
        direct_ok = post(GEMINI, chat, {"Authorization": f"Bearer {gemini_key}"})
        direct_header = post(
            GEMINI,
            chat,
            {"Authorization": f"Bearer {gemini_key}", "x-goog-api-key": INVALID},
        )
        direct_bad_only = post(GEMINI, chat, {"x-goog-api-key": INVALID})
        sy_ok = post(f"{SY}/v1/chat/completions", chat, {})
        sy_header = post(
            f"{SY}/v1/chat/completions",
            chat,
            {"x-goog-api-key": INVALID},
        )
        sy_body = post(
            f"{SY}/v1/chat/completions",
            {**chat, "api_key": INVALID},
            {},
        )
        bundle = {
            "run": run,
            "direct_ok": {"status": direct_ok["status"], "leaks": leak_hits(direct_ok["body"], secrets), "body": redact(direct_ok["body"], secrets)[:500]},
            "direct_bearer_plus_invalid_x_goog": {"status": direct_header["status"], "leaks": leak_hits(direct_header["body"], secrets), "body": redact(direct_header["body"], secrets)[:500]},
            "direct_invalid_x_goog_only": {"status": direct_bad_only["status"], "leaks": leak_hits(direct_bad_only["body"], secrets), "body": redact(direct_bad_only["body"], secrets)[:500]},
            "switchyard_ok": {"status": sy_ok["status"], "leaks": leak_hits(sy_ok["body"], secrets), "body": redact(sy_ok["body"], secrets)[:500]},
            "switchyard_x_goog_header": {"status": sy_header["status"], "leaks": leak_hits(sy_header["body"], secrets), "body": redact(sy_header["body"], secrets)[:500]},
            "switchyard_api_key_body": {"status": sy_body["status"], "leaks": leak_hits(sy_body["body"], secrets), "body": redact(sy_body["body"], secrets)[:500]},
        }
        results.append(bundle)
        print(
            f"run={run} direct_ok={direct_ok['status']} "
            f"direct_both={direct_header['status']} "
            f"direct_xgoog={direct_bad_only['status']} "
            f"sy_ok={sy_ok['status']} sy_hdr={sy_header['status']} sy_body={sy_body['status']}"
        )
    (OUT / "live-results.json").write_text(json.dumps(results, indent=2))
    print("wrote live-results.json")


if __name__ == "__main__":
    main()
