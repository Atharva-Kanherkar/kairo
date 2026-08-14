#!/usr/bin/env python3
"""Round 2: closed-port pass-through 502s, and live resumable upload via x-pass- headers."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path("transcripts/028")
N = 5
SECRET_NAMES = ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")
CANARIES = ["CANARY_GEMINI_PASSTHROUGH_KEY", "CANARY_CLOSED_PORT_KEY"]

LL_LIVE = "http://127.0.0.1:4001"
LL_CLOSED_CANARY = "http://127.0.0.1:4003"
LL_CLOSED_LIVE = "http://127.0.0.1:4004"
GOOGLE = "https://generativelanguage.googleapis.com"


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
    for c in CANARIES:
        if c in out:
            out = out.replace(c, "REDACTED_CANARY")
    return out


def leak_hits(text: str, secrets: dict[str, str]) -> list[str]:
    hits = []
    for name, val in secrets.items():
        if not val or len(val) < 8:
            continue
        if val in text:
            hits.append(f"FULL:{name}")
        elif len(val) > 12 and val[:4] in text and val[-4:] in text:
            hits.append(f"PREFIX_SUFFIX:{name}")
    for c in CANARIES:
        if c in text:
            hits.append(f"CANARY:{c}")
    return hits


def blob(headers: dict, body: str) -> str:
    return "\n".join(f"{k}: {v}" for k, v in headers.items()) + "\n\n" + body


def call(
    method: str,
    url: str,
    payload: dict | None = None,
    headers: dict | None = None,
    raw: bytes | None = None,
    timeout: float = 20.0,
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
    return {
        "status": resp.get("status"),
        "leaks": leak_hits(text, secrets),
        "leak_headers": [k for k, v in (resp.get("headers") or {}).items() if leak_hits(v, secrets)],
        "body": redact(resp.get("body") or "", secrets)[:1500],
        "headers": {k: redact(v, secrets) for k, v in (resp.get("headers") or {}).items()},
    }


def main() -> None:
    env = load_env()
    secrets = {k: env.get(k) or "" for k in SECRET_NAMES}
    secrets = {k: v for k, v in secrets.items() if v}
    gemini = env.get("GEMINI_API_KEY") or ""

    gen_body = b'{"contents":[{"role":"user","parts":[{"text":"ping"}]}]}'
    upload_body = b'{"file":{"display_name":"kairo-round2"}}'
    resumable = {
        "x-pass-x-goog-upload-protocol": "resumable",
        "x-pass-x-goog-upload-command": "start",
        "x-pass-x-goog-upload-header-content-length": "8",
        "x-pass-x-goog-upload-header-content-type": "text/plain",
        "content-type": "application/json",
    }
    direct_resumable = {
        "x-goog-upload-protocol": "resumable",
        "x-goog-upload-command": "start",
        "x-goog-upload-header-content-length": "8",
        "x-goog-upload-header-content-type": "text/plain",
        "content-type": "application/json",
    }

    rows = []
    for run in range(1, N + 1):
        probes = [
            (
                "closed_canary_pt_generate",
                "POST",
                LL_CLOSED_CANARY + "/gemini/v1beta/models/gemini-2.5-flash:generateContent",
                None,
                None,
                gen_body,
            ),
            (
                "closed_live_pt_generate",
                "POST",
                LL_CLOSED_LIVE + "/gemini/v1beta/models/gemini-2.5-flash:generateContent",
                None,
                None,
                gen_body,
            ),
            (
                "closed_canary_pt_models",
                "GET",
                LL_CLOSED_CANARY + "/gemini/v1beta/models",
                None,
                None,
                None,
            ),
            (
                "closed_live_pt_models",
                "GET",
                LL_CLOSED_LIVE + "/gemini/v1beta/models",
                None,
                None,
                None,
            ),
            (
                "live_pt_upload_xpass_resumable",
                "POST",
                LL_LIVE + "/gemini/upload/v1beta/files",
                None,
                resumable,
                upload_body,
            ),
            (
                "live_pt_upload_plain",
                "POST",
                LL_LIVE + "/gemini/upload/v1beta/files",
                None,
                {"content-type": "application/json"},
                upload_body,
            ),
            (
                "direct_google_resumable",
                "POST",
                GOOGLE + "/upload/v1beta/files?key=" + gemini,
                None,
                direct_resumable,
                upload_body,
            ),
            (
                "live_chat_control",
                "POST",
                LL_LIVE + "/v1/chat/completions",
                {"model": "gemini-flash", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 4},
                None,
                None,
            ),
        ]
        for name, method, url, payload, headers, raw in probes:
            resp = call(method, url, payload=payload, headers=headers, raw=raw)
            rows.append({"probe": name, "run": run, **slim(resp, secrets)})

    (OUT / "round2-results.json").write_text(json.dumps(rows, indent=2))
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
    (OUT / "round2-scoreboard.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
