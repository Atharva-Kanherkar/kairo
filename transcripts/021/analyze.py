#!/usr/bin/env python3
"""summarize switchyard header/body canary hits."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

OUT = Path("transcripts/021")
CANARIES = [
    "CANARY_X_GOOG_API_KEY",
    "CANARY_X_CUSTOM_SECRET",
    "CANARY_X_API_KEY",
    "CANARY_AUTHORIZATION",
    "CANARY_COOKIE",
    "CANARY_BODY_API_KEY",
]


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def tag(rec: dict) -> str:
    body = rec.get("body") or {}
    msgs = body.get("messages") if isinstance(body, dict) else None
    if isinstance(msgs, list) and msgs:
        c = msgs[0].get("content") if isinstance(msgs[0], dict) else ""
        if isinstance(c, str):
            return c.split(" run=")[0]
        if isinstance(c, list) and c:
            t = c[0].get("text") if isinstance(c[0], dict) else ""
            if isinstance(t, str):
                return t.split(" run=")[0]
    return rec.get("path", "?")


def interesting(headers: dict) -> dict:
    out = {}
    for k, v in headers.items():
        kl = k.lower()
        if kl.startswith("x-") or kl in {
            "authorization",
            "cookie",
            "anthropic-beta",
            "anthropic-version",
        }:
            out[k] = v
    return out


def main() -> None:
    clients = json.loads((OUT / "client-results.json").read_text())
    by = defaultdict(list)
    for row in clients:
        by[row["probe"]].append(row["client"].get("status"))
    print("=== client status ===")
    for k, v in by.items():
        print(f"{k}: {v}")

    caps = load_jsonl(OUT / "mock.jsonl")
    print(f"\n=== upstream captures n={len(caps)} ===")
    for rec in caps:
        text = json.dumps(rec)
        hits = [c for c in CANARIES if c in text]
        body = rec.get("body") or {}
        extra = {}
        if isinstance(body, dict):
            for k in ("api_key", "api_base"):
                if k in body:
                    extra[k] = body[k]
        print(
            f"probe={tag(rec)!r} path={rec.get('path')} canaries={hits} "
            f"headers={interesting(rec.get('headers') or {})} body_fields={extra}"
        )


if __name__ == "__main__":
    main()
