#!/usr/bin/env python3
"""Live Gemini/OpenAI vs Switchyard reserved-header misses. Redacts real keys."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path("transcripts/023")
SY_GEM = "http://127.0.0.1:9002"
SY_OAI = "http://127.0.0.1:9003"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
OPENAI = "https://api.openai.com/v1/chat/completions"
N = 5
INVALID_ORG = "org-CANARYINVALIDORG"
INVALID_API_KEY = "CANARY_AZURE_API_KEY"


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
        if s and len(s) > 12 and s[:8] in text and s not in text:
            hits.append(f"PREFIX_{i}")
    return hits


def post(url: str, payload: dict, headers: dict | None = None, timeout: float = 40.0) -> dict:
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
        "body": redact(resp["body"], secrets)[:800],
    }


def main() -> None:
    env = load_env()
    secrets = [env.get(k) or "" for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")]
    secrets = [s for s in secrets if s]
    gem = env.get("GEMINI_API_KEY") or ""
    oai = env.get("OPENAI_API_KEY") or ""

    gem_body = {
        "model": "gemini-2.5-flash",
        "messages": [{"role": "user", "content": "Reply with the single word ping."}],
        "max_tokens": 8,
    }
    sy_gem_body = {**gem_body, "model": "gemini-flash"}
    oai_body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Reply with the single word ping."}],
        "max_tokens": 8,
    }

    results = []
    for run in range(1, N + 1):
        bundle = {
            "run": run,
            "direct_gemini_ok": slim(post(GEMINI, gem_body, {"Authorization": f"Bearer {gem}"}), secrets),
            "direct_gemini_api_key_invalid": slim(
                post(GEMINI, gem_body, {"Authorization": f"Bearer {gem}", "api-key": INVALID_API_KEY}),
                secrets,
            ),
            "sy_gemini_ok": slim(post(f"{SY_GEM}/v1/chat/completions", sy_gem_body), secrets),
            "sy_gemini_api_key": slim(
                post(f"{SY_GEM}/v1/chat/completions", sy_gem_body, {"api-key": INVALID_API_KEY}),
                secrets,
            ),
            "sy_gemini_org": slim(
                post(
                    f"{SY_GEM}/v1/chat/completions",
                    sy_gem_body,
                    {"OpenAI-Organization": INVALID_ORG},
                ),
                secrets,
            ),
            "direct_openai_ok": slim(post(OPENAI, oai_body, {"Authorization": f"Bearer {oai}"}), secrets),
            "direct_openai_invalid_org": slim(
                post(
                    OPENAI,
                    oai_body,
                    {"Authorization": f"Bearer {oai}", "OpenAI-Organization": INVALID_ORG},
                ),
                secrets,
            ),
            "direct_openai_api_key_invalid": slim(
                post(OPENAI, oai_body, {"Authorization": f"Bearer {oai}", "api-key": INVALID_API_KEY}),
                secrets,
            ),
            "sy_openai_ok": slim(post(f"{SY_OAI}/v1/chat/completions", oai_body), secrets),
            "sy_openai_invalid_org": slim(
                post(
                    f"{SY_OAI}/v1/chat/completions",
                    oai_body,
                    {"OpenAI-Organization": INVALID_ORG},
                ),
                secrets,
            ),
            "sy_openai_api_key": slim(
                post(f"{SY_OAI}/v1/chat/completions", oai_body, {"api-key": INVALID_API_KEY}),
                secrets,
            ),
        }
        results.append(bundle)
        print(
            "run={run} "
            "g_ok={g} g_apikey={ga} sy_g_ok={sgo} sy_g_apikey={sga} sy_g_org={sgo2} "
            "o_ok={o} o_org={oo} o_apikey={oa} sy_o_ok={so} sy_o_org={soo} sy_o_apikey={soa}".format(
                run=run,
                g=bundle["direct_gemini_ok"]["status"],
                ga=bundle["direct_gemini_api_key_invalid"]["status"],
                sgo=bundle["sy_gemini_ok"]["status"],
                sga=bundle["sy_gemini_api_key"]["status"],
                sgo2=bundle["sy_gemini_org"]["status"],
                o=bundle["direct_openai_ok"]["status"],
                oo=bundle["direct_openai_invalid_org"]["status"],
                oa=bundle["direct_openai_api_key_invalid"]["status"],
                so=bundle["sy_openai_ok"]["status"],
                soo=bundle["sy_openai_invalid_org"]["status"],
                soa=bundle["sy_openai_api_key"]["status"],
            )
        )
    (OUT / "live-results.json").write_text(json.dumps(results, indent=2))
    print("wrote live-results.json")


if __name__ == "__main__":
    main()
