#!/usr/bin/env python3
"""Switchyard credential-leak hunt. Never writes real .env values."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path("transcripts/025")
SY = os.environ.get("SY", "http://127.0.0.1:9000")
N = int(os.environ.get("N", "5"))
PHASE = os.environ.get("PHASE", "phase")

CANARIES = [
    "CANARY_ADMIN_QUERY_KEY",
    "CANARY_ADMIN_HEADER_KEY",
    "CANARY_ADMIN_ENV_KEY",
    "CANARY_ADMIN_USERINFO_KEY",
    "CANARY_EXTRA_HEADER",
    "CANARY_USER_BEARER",
    "CANARY_USER_JSON_KEY",
    "CANARY_USER_X_API_KEY",
    "CANARY_USER_API_KEY_HEADER",
    "CANARY_GEMINI_QUERY_KEY",
    "sk-hunt-fake-openai-key-CANARY",
    "AIzaSyCANARYFAKEGEMINIKEY00000000000",
    "SWITCHYARD_HUNT_ADMIN_KEY",
]


def load_env_secrets() -> dict[str, str]:
    secrets: dict[str, str] = {}
    env_path = Path(".env")
    if env_path.exists():
        for raw in env_path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            v = v.strip().strip("'").strip('"')
            if v and (k.endswith("_KEY") or k.endswith("_TOKEN")):
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
            rh = {k.lower(): v for k, v in resp.headers.items()}
            return {"ok": True, "status": resp.status, "body": body, "resp_headers": rh}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        rh = {k.lower(): v for k, v in e.headers.items()}
        return {"ok": False, "status": e.code, "body": body, "resp_headers": rh}
    except Exception as e:
        return {"ok": False, "status": None, "body": f"{type(e).__name__}: {e}", "resp_headers": {}}


def chat(tag: str, extra: dict | None = None, model: str = "captured-model") -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": tag}],
        "max_tokens": 8,
    }
    if extra:
        payload.update(extra)
    return payload


def summarize(tag: str, resp: dict, secrets: dict[str, str]) -> dict:
    blob = (resp.get("body") or "") + json.dumps(resp.get("resp_headers") or {})
    hits = scan(blob, secrets)
    snippet = redact(resp.get("body") or "", secrets)
    if len(snippet) > 2500:
        snippet = snippet[:2500] + "...truncated..."
    return {
        "phase": PHASE,
        "tag": tag,
        "status": resp.get("status"),
        "ok": resp.get("ok"),
        "hits": hits,
        "body": snippet,
    }


def main() -> None:
    secrets = load_env_secrets()
    model = os.environ.get("MODEL", "captured-model")
    results: list[dict] = []
    for run in range(1, N + 1):
        results.append(summarize(f"health r{run}", request("GET", SY + "/health"), secrets))
        results.append(summarize(f"models r{run}", request("GET", SY + "/v1/models"), secrets))
        results.append(summarize(f"stats r{run}", request("GET", SY + "/v1/stats"), secrets))
        results.append(summarize(f"metrics r{run}", request("GET", SY + "/metrics"), secrets))
        results.append(
            summarize(
                f"chat_plain r{run}",
                request("POST", SY + "/v1/chat/completions", chat(f"plain {run}", model=model)),
                secrets,
            )
        )
        results.append(
            summarize(
                f"chat_user_bearer r{run}",
                request(
                    "POST",
                    SY + "/v1/chat/completions",
                    chat(f"bearer {run}", model=model),
                    {"Authorization": "Bearer CANARY_USER_BEARER"},
                ),
                secrets,
            )
        )
        results.append(
            summarize(
                f"chat_user_x_api_key r{run}",
                request(
                    "POST",
                    SY + "/v1/chat/completions",
                    chat(f"xapi {run}", model=model),
                    {"x-api-key": "CANARY_USER_X_API_KEY"},
                ),
                secrets,
            )
        )
        results.append(
            summarize(
                f"chat_user_api_key_hdr r{run}",
                request(
                    "POST",
                    SY + "/v1/chat/completions",
                    chat(f"apikeyhdr {run}", model=model),
                    {"api-key": "CANARY_USER_API_KEY_HEADER"},
                ),
                secrets,
            )
        )
        results.append(
            summarize(
                f"chat_user_json_key r{run}",
                request(
                    "POST",
                    SY + "/v1/chat/completions",
                    chat(f"jsonkey {run}", extra={"api_key": "CANARY_USER_JSON_KEY"}, model=model),
                ),
                secrets,
            )
        )
        results.append(
            summarize(
                f"chat_stream r{run}",
                request(
                    "POST",
                    SY + "/v1/chat/completions",
                    chat(f"stream {run}", extra={"stream": True}, model=model),
                ),
                secrets,
            )
        )
        results.append(
            summarize(
                f"messages r{run}",
                request(
                    "POST",
                    SY + "/v1/messages",
                    {
                        "model": model,
                        "max_tokens": 8,
                        "messages": [{"role": "user", "content": f"anth {run}"}],
                    },
                    {"x-api-key": "CANARY_USER_X_API_KEY"},
                ),
                secrets,
            )
        )

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{PHASE}-results.json"
    existing = json.loads(path.read_text()) if path.exists() else []
    existing.extend(results)
    path.write_text(json.dumps(existing, indent=2) + "\n")

    by: dict[str, dict] = {}
    for row in results:
        base = row["tag"].rsplit(" r", 1)[0]
        slot = by.setdefault(base, {"n": 0, "hits": set(), "statuses": set()})
        slot["n"] += 1
        slot["hits"].update(row["hits"])
        slot["statuses"].add(row["status"])
    print("PHASE", PHASE, "SY", SY, "MODEL", model)
    print("secret_names", sorted(secrets))
    for tag, slot in by.items():
        print(f"{tag}\tn={slot['n']}\tstatus={sorted(slot['statuses'], key=lambda x: (x is None, x))}\thits={sorted(slot['hits'])}")


if __name__ == "__main__":
    main()
