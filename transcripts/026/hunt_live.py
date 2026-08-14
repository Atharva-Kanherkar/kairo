#!/usr/bin/env python3
"""Live OpenAI/Gemini: client organization and extra_headers. Redacts real keys."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path("transcripts/026")
LL = "http://127.0.0.1:4002"
OPENAI = "https://api.openai.com/v1/chat/completions"
N = 5
INVALID_ORG = "org-CANARYINVALIDORG"
INVALID_BEARER = "CANARY_INVALID_BEARER"
INVALID_GOOG = "CANARY_INVALID_X_GOOG"


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
            hits.append(f"FULL_{i}")
        elif s and len(s) > 12 and s[:8] in text:
            hits.append(f"PREFIX_{i}")
    return hits


def post(url: str, payload: dict, headers: dict | None = None, timeout: float = 45.0) -> dict:
    body = json.dumps(payload).encode()
    hdrs = {"content-type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": True, "status": resp.status, "body": resp.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "body": e.read().decode("utf-8", "replace")}
    except Exception as e:
        return {"ok": False, "status": None, "body": f"{type(e).__name__}: {e}"}


def slim(resp: dict, secrets: list[str]) -> dict:
    return {
        "status": resp["status"],
        "leaks": leak_hits(resp["body"], secrets),
        "body": redact(resp["body"], secrets)[:900],
    }


def main() -> None:
    env = load_env()
    secrets = [env.get(k) or "" for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")]
    secrets = [s for s in secrets if s]
    oai = env.get("OPENAI_API_KEY") or ""

    ping = [{"role": "user", "content": "Reply with the single word ping."}]
    rows = []
    for run in range(1, N + 1):
        probes = [
            (
                "ll_openai_control",
                LL + "/v1/chat/completions",
                {"model": "openai-mini", "messages": ping, "max_tokens": 8},
                None,
            ),
            (
                "ll_openai_organization",
                LL + "/v1/chat/completions",
                {
                    "model": "openai-mini",
                    "messages": ping,
                    "max_tokens": 8,
                    "organization": INVALID_ORG,
                },
                None,
            ),
            (
                "ll_openai_extra_headers_org",
                LL + "/v1/chat/completions",
                {
                    "model": "openai-mini",
                    "messages": ping,
                    "max_tokens": 8,
                    "extra_headers": {"OpenAI-Organization": INVALID_ORG},
                },
                None,
            ),
            (
                "direct_openai_control",
                OPENAI,
                {"model": "gpt-4o-mini", "messages": ping, "max_tokens": 8},
                {"authorization": f"Bearer {oai}"},
            ),
            (
                "direct_openai_org",
                OPENAI,
                {"model": "gpt-4o-mini", "messages": ping, "max_tokens": 8},
                {
                    "authorization": f"Bearer {oai}",
                    "openai-organization": INVALID_ORG,
                },
            ),
            (
                "ll_gemini_control",
                LL + "/v1/chat/completions",
                {"model": "gemini-flash", "messages": ping, "max_tokens": 8},
                None,
            ),
            (
                "ll_gemini_extra_headers_auth",
                LL + "/v1/chat/completions",
                {
                    "model": "gemini-flash",
                    "messages": ping,
                    "max_tokens": 8,
                    "extra_headers": {"Authorization": f"Bearer {INVALID_BEARER}"},
                },
                None,
            ),
            (
                "ll_gemini_extra_headers_goog",
                LL + "/v1/chat/completions",
                {
                    "model": "gemini-flash",
                    "messages": ping,
                    "max_tokens": 8,
                    "extra_headers": {"x-goog-api-key": INVALID_GOOG},
                },
                None,
            ),
        ]
        for name, url, payload, headers in probes:
            resp = post(url, payload, headers=headers)
            rows.append({"probe": name, "run": run, **slim(resp, secrets)})

    (OUT / "live-results.json").write_text(json.dumps(rows, indent=2))
    summary = {}
    for row in rows:
        b = summary.setdefault(row["probe"], {"n": 0, "statuses": {}, "leak_runs": 0})
        b["n"] += 1
        st = str(row["status"])
        b["statuses"][st] = b["statuses"].get(st, 0) + 1
        if row["leaks"]:
            b["leak_runs"] += 1
    (OUT / "live-scoreboard.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
