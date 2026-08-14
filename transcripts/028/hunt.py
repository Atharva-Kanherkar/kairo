#!/usr/bin/env python3
"""High-sev key-leak hunt. Canaries only. Scans response headers and bodies."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path("transcripts/028")
LL = "http://127.0.0.1:4000"
SY = "http://127.0.0.1:9000"
N = 5

CANARIES = [
    "CANARY_GEMINI_PASSTHROUGH_KEY",
    "CANARY_ANTHROPIC_PASSTHROUGH_KEY",
    "CANARY_DEPLOYMENT_API_KEY",
    "CANARY_SY_EXTRA_X_GOOG",
    "DEPLOYMENT_BEARER",
]


def blob(headers: dict, body: str) -> str:
    hdr = "\n".join(f"{k}: {v}" for k, v in headers.items())
    return hdr + "\n\n" + body


def hits(text: str) -> list[str]:
    return [c for c in CANARIES if c in text]


def call(
    method: str,
    url: str,
    payload: dict | None = None,
    headers: dict | None = None,
    timeout: float = 20.0,
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


def slim(resp: dict) -> dict:
    text = blob(resp.get("headers") or {}, resp.get("body") or "")
    return {
        "status": resp.get("status"),
        "hits": hits(text),
        "hit_headers": [k for k, v in (resp.get("headers") or {}).items() if hits(v)],
        "body": (resp.get("body") or "")[:1200],
        "headers": resp.get("headers") or {},
    }


def main() -> None:
    rows = []
    chat = {
        "model": "mock",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
    }
    gemini_body = json.dumps(
        {"contents": [{"role": "user", "parts": [{"text": "ping"}]}]}
    ).encode()
    upload_headers = {
        "x-goog-upload-protocol": "resumable",
        "x-goog-upload-command": "start",
        "x-goog-upload-header-content-type": "text/plain",
        "content-type": "application/json",
    }

    dump_gets = [
        "/health",
        "/health/liveliness",
        "/health/readiness",
        "/v1/models",
        "/model/info",
        "/settings",
        "/metrics",
        "/key/info",
        "/spend/logs",
        "/config/yaml",
        "/routes",
        "/debug/asyncio-tasks",
        "/debug/memory/summary",
        "/utils/supported_openai_params?model=mock",
    ]

    for run in range(1, N + 1):
        for path in dump_gets:
            resp = call("GET", LL + path)
            rows.append({"probe": f"ll_get_{path.strip('/').replace('/', '_')}", "run": run, **slim(resp)})

        rows.append({"probe": "ll_chat_control", "run": run, **slim(call("POST", LL + "/v1/chat/completions", chat))})

        rows.append(
            {
                "probe": "ll_chat_bad_model",
                "run": run,
                **slim(
                    call(
                        "POST",
                        LL + "/v1/chat/completions",
                        {
                            "model": "does-not-exist-canary",
                            "messages": [{"role": "user", "content": "ping"}],
                            "max_tokens": 8,
                        },
                    )
                ),
            }
        )

        rows.append(
            {
                "probe": "ll_transform_request",
                "run": run,
                **slim(
                    call(
                        "POST",
                        LL + "/utils/transform_request",
                        {
                            "call_type": "completion",
                            "request_body": {
                                "model": "gemini/gemini-2.5-flash",
                                "messages": [{"role": "user", "content": "ping"}],
                            },
                        },
                    )
                ),
            }
        )

        rows.append(
            {
                "probe": "ll_gemini_pt_generate",
                "run": run,
                **slim(
                    call(
                        "POST",
                        LL + "/gemini/v1beta/models/gemini-2.5-flash:generateContent",
                        raw=gemini_body,
                    )
                ),
            }
        )

        rows.append(
            {
                "probe": "ll_gemini_pt_upload",
                "run": run,
                **slim(
                    call(
                        "POST",
                        LL + "/gemini/upload/v1beta/files",
                        raw=b'{"file":{"display_name":"canary"}}',
                        headers=upload_headers,
                    )
                ),
            }
        )

        rows.append(
            {
                "probe": "ll_gemini_pt_models",
                "run": run,
                **slim(call("GET", LL + "/gemini/v1beta/models")),
            }
        )

        rows.append(
            {
                "probe": "ll_anthropic_pt",
                "run": run,
                **slim(
                    call(
                        "POST",
                        LL + "/anthropic/v1/messages",
                        {
                            "model": "claude-3-5-haiku-latest",
                            "max_tokens": 8,
                            "messages": [{"role": "user", "content": "ping"}],
                        },
                    )
                ),
            }
        )

        # Switchyard dump + chat + leftover secret headers
        for path in ("/health", "/v1/models", "/v1/stats", "/metrics"):
            rows.append({"probe": f"sy_get_{path.strip('/').replace('/', '_')}", "run": run, **slim(call("GET", SY + path))})

        rows.append(
            {
                "probe": "sy_chat_control",
                "run": run,
                **slim(
                    call(
                        "POST",
                        SY + "/v1/chat/completions",
                        {
                            "model": "captured-model",
                            "messages": [{"role": "user", "content": "ping"}],
                            "max_tokens": 8,
                        },
                    )
                ),
            }
        )
        rows.append(
            {
                "probe": "sy_header_x_amz_security_token",
                "run": run,
                **slim(
                    call(
                        "POST",
                        SY + "/v1/chat/completions",
                        {
                            "model": "captured-model",
                            "messages": [{"role": "user", "content": "ping"}],
                            "max_tokens": 8,
                        },
                        headers={"x-amz-security-token": "CANARY_SY_EXTRA_X_GOOG"},
                    )
                ),
            }
        )

    (OUT / "client-results.json").write_text(json.dumps(rows, indent=2))
    summary = {}
    for row in rows:
        b = summary.setdefault(row["probe"], {"n": 0, "statuses": {}, "hit_runs": 0, "hit_headers": {}})
        b["n"] += 1
        st = str(row["status"])
        b["statuses"][st] = b["statuses"].get(st, 0) + 1
        if row["hits"]:
            b["hit_runs"] += 1
            b.setdefault("hits", sorted(set(row["hits"])))
        for h in row["hit_headers"]:
            b["hit_headers"][h] = b["hit_headers"].get(h, 0) + 1
    (OUT / "scoreboard.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
