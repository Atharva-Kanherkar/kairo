#!/usr/bin/env python3
"""Live key leak hunt. Uses .env. Redacts secrets in saved transcripts."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path("transcripts/028")
LL = "http://127.0.0.1:4001"
SY = "http://127.0.0.1:9001"
N = 5
SECRET_NAMES = ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")


def load_env() -> dict[str, str]:
    env = {}
    for line in Path(".env").read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    for k in SECRET_NAMES:
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def redact(text: str, secrets: dict[str, str]) -> str:
    out = text
    for name, val in secrets.items():
        if val:
            out = out.replace(val, f"REDACTED_{name}")
    return out


def leak_hits(text: str, secrets: dict[str, str]) -> list[str]:
    hits = []
    for name, val in secrets.items():
        if not val or len(val) < 8:
            continue
        if val in text:
            hits.append(f"FULL:{name}")
        elif val[:4] in text and val[-4:] in text:
            hits.append(f"PREFIX_SUFFIX:{name}")
    return hits


def blob(headers: dict, body: str) -> str:
    hdr = "\n".join(f"{k}: {v}" for k, v in headers.items())
    return hdr + "\n\n" + body


def call(
    method: str,
    url: str,
    payload: dict | None = None,
    headers: dict | None = None,
    timeout: float = 45.0,
    raw: bytes | None = None,
) -> dict:
    data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    hdrs = {}
    if data is not None:
        hdrs["content-type"] = "application/json"
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            rh = {k: v for k, v in resp.headers.items()}
            return {"ok": True, "status": resp.status, "headers": rh, "body": body}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        rh = {k: v for k, v in e.headers.items()} if e.headers else {}
        return {"ok": False, "status": e.code, "headers": rh, "body": body}
    except Exception as e:
        return {"ok": False, "status": None, "headers": {}, "body": f"{type(e).__name__}: {e}"}


def slim(resp: dict, secrets: dict[str, str]) -> dict:
    text = blob(resp.get("headers") or {}, resp.get("body") or "")
    redacted_headers = {k: redact(v, secrets) for k, v in (resp.get("headers") or {}).items()}
    return {
        "status": resp.get("status"),
        "leaks": leak_hits(text, secrets),
        "leak_headers": [k for k, v in (resp.get("headers") or {}).items() if leak_hits(v, secrets)],
        "body": redact(resp.get("body") or "", secrets)[:1500],
        "headers": redacted_headers,
    }


def main() -> None:
    env = load_env()
    secrets = {k: env.get(k) or "" for k in SECRET_NAMES}
    secrets = {k: v for k, v in secrets.items() if v}

    ping = [{"role": "user", "content": "Reply with the single word ping."}]
    gemini_ok = json.dumps({"contents": [{"role": "user", "parts": [{"text": "ping"}]}]}).encode()
    gemini_bad = json.dumps({"contents": []}).encode()
    upload_headers = {
        "x-goog-upload-protocol": "resumable",
        "x-goog-upload-command": "start",
        "x-goog-upload-header-content-type": "text/plain",
        "content-type": "application/json",
    }

    rows = []
    for run in range(1, N + 1):
        probes = [
            ("ll_health", "GET", LL + "/health", None, None, None),
            ("ll_model_info", "GET", LL + "/model/info", None, None, None),
            ("ll_models", "GET", LL + "/v1/models", None, None, None),
            ("ll_settings", "GET", LL + "/settings", None, None, None),
            (
                "ll_chat_gemini_ok",
                "POST",
                LL + "/v1/chat/completions",
                {"model": "gemini-flash", "messages": ping, "max_tokens": 8},
                None,
                None,
            ),
            (
                "ll_chat_openai_ok",
                "POST",
                LL + "/v1/chat/completions",
                {"model": "openai-mini", "messages": ping, "max_tokens": 8},
                None,
                None,
            ),
            (
                "ll_chat_gemini_bad_model",
                "POST",
                LL + "/v1/chat/completions",
                {
                    "model": "gemini-flash-does-not-exist",
                    "messages": ping,
                    "max_tokens": 8,
                },
                None,
                None,
            ),
            (
                "ll_transform_gemini",
                "POST",
                LL + "/utils/transform_request",
                {
                    "call_type": "completion",
                    "request_body": {
                        "model": "gemini/gemini-2.5-flash",
                        "messages": ping,
                    },
                },
                None,
                None,
            ),
            ("ll_gemini_pt_models", "GET", LL + "/gemini/v1beta/models", None, None, None),
            (
                "ll_gemini_pt_generate_ok",
                "POST",
                LL + "/gemini/v1beta/models/gemini-2.5-flash:generateContent",
                None,
                None,
                gemini_ok,
            ),
            (
                "ll_gemini_pt_generate_bad",
                "POST",
                LL + "/gemini/v1beta/models/gemini-2.5-flash:generateContent",
                None,
                None,
                gemini_bad,
            ),
            (
                "ll_gemini_pt_upload",
                "POST",
                LL + "/gemini/upload/v1beta/files",
                None,
                upload_headers,
                b'{"file":{"display_name":"kairo-canary"}}',
            ),
            (
                "ll_anthropic_pt_bad",
                "POST",
                LL + "/anthropic/v1/messages",
                {
                    "model": "claude-3-5-haiku-latest",
                    "max_tokens": 8,
                    "messages": [{"role": "user", "content": "ping"}],
                },
                None,
                None,
            ),
            ("sy_health", "GET", SY + "/health", None, None, None),
            ("sy_models", "GET", SY + "/v1/models", None, None, None),
            ("sy_stats", "GET", SY + "/v1/stats", None, None, None),
            (
                "sy_chat_ok",
                "POST",
                SY + "/v1/chat/completions",
                {"model": "gemini-flash", "messages": ping, "max_tokens": 8},
                None,
                None,
            ),
            (
                "sy_chat_bad_model",
                "POST",
                SY + "/v1/chat/completions",
                {"model": "does-not-exist", "messages": ping, "max_tokens": 8},
                None,
                None,
            ),
        ]
        for name, method, url, payload, headers, raw in probes:
            resp = call(method, url, payload=payload, headers=headers, raw=raw)
            rows.append({"probe": name, "run": run, **slim(resp, secrets)})

    (OUT / "live-results.json").write_text(json.dumps(rows, indent=2))
    summary = {}
    for row in rows:
        b = summary.setdefault(row["probe"], {"n": 0, "statuses": {}, "leak_runs": 0, "leak_headers": {}})
        b["n"] += 1
        st = str(row["status"])
        b["statuses"][st] = b["statuses"].get(st, 0) + 1
        if row["leaks"]:
            b["leak_runs"] += 1
            b.setdefault("leaks", sorted(set(row["leaks"])))
        for h in row["leak_headers"]:
            b["leak_headers"][h] = b["leak_headers"].get(h, 0) + 1
    (OUT / "live-scoreboard.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
