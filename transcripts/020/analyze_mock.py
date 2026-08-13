#!/usr/bin/env python3
"""Summarize mock-hunt captures. Prints canary hits, never real secrets."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

OUT = Path("transcripts/020")
CANARIES = [
    "CANARY_X_GOOG_API_KEY",
    "CANARY_X_CUSTOM_SECRET",
    "CANARY_X_API_KEY",
    "CANARY_BODY_API_KEY",
    "CANARY_AUTHORIZATION",
    "BYPASS_MOCK_RESPONSE",
    "sk-x",
]


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def dump(rec: dict) -> str:
    return json.dumps(rec, sort_keys=True)


def canaries_in(text: str) -> list[str]:
    return [c for c in CANARIES if c in text]


def probe_of(rec: dict) -> str:
    body = rec.get("body") or {}
    msgs = body.get("messages") if isinstance(body, dict) else None
    if isinstance(msgs, list) and msgs:
        content = msgs[0].get("content") if isinstance(msgs[0], dict) else ""
        if isinstance(content, str):
            return content.split(" run=")[0]
    # responses API
    inp = body.get("input") if isinstance(body, dict) else None
    if isinstance(inp, str):
        return inp.split(" run=")[0]
    if isinstance(inp, list) and inp:
        first = inp[0]
        if isinstance(first, dict):
            c = first.get("content")
            if isinstance(c, str):
                return c.split(" run=")[0]
    return rec.get("path", "?")


def header_subset(headers: dict) -> dict:
    interesting = {}
    for k, v in headers.items():
        kl = k.lower()
        if kl.startswith("x-") or kl in {
            "authorization",
            "anthropic-beta",
            "openai-organization",
        }:
            interesting[k] = v
    return interesting


def main() -> None:
    primary = load_jsonl(OUT / "mock-primary.jsonl")
    redirect = load_jsonl(OUT / "mock-redirect.jsonl")
    clients = json.loads((OUT / "client-results.json").read_text())

    print("=== client HTTP status by probe ===")
    by_probe: dict[str, list] = defaultdict(list)
    for row in clients:
        by_probe[row["probe"]].append(row["client"])
    for probe, runs in by_probe.items():
        statuses = [r.get("status") for r in runs]
        bodies = []
        for r in runs:
            b = r.get("body") or ""
            if "BYPASS_MOCK_RESPONSE" in b:
                bodies.append("CONTAINS_BYPASS")
            elif "error" in b.lower()[:80] or r.get("ok") is False:
                bodies.append(b[:180].replace("\n", " "))
            else:
                bodies.append("ok-or-completion")
        print(f"{probe}: statuses={statuses}")
        if probe == "litellm_models":
            print(f"  models body: {runs[0].get('body','')[:500]}")
        uniq = set(bodies)
        if len(uniq) == 1:
            print(f"  body_kind: {next(iter(uniq))}")
        else:
            print(f"  body_kinds: {uniq}")

    print("\n=== primary mock captures (n={}) ===".format(len(primary)))
    for rec in primary:
        text = dump(rec)
        hits = canaries_in(text)
        print(
            f"probe={probe_of(rec)!r} path={rec.get('path')} "
            f"canaries={hits} interesting_headers={header_subset(rec.get('headers') or {})}"
        )
        body = rec.get("body") or {}
        extra = {
            k: body.get(k)
            for k in ("api_base", "api_key", "mock_response", "disable_global_guardrails")
            if isinstance(body, dict) and k in body
        }
        if extra:
            print(f"  forwarded_body_fields={extra}")

    print("\n=== redirect mock captures (n={}) ===".format(len(redirect)))
    for rec in redirect:
        text = dump(rec)
        hits = canaries_in(text)
        print(
            f"probe={probe_of(rec)!r} path={rec.get('path')} "
            f"canaries={hits} interesting_headers={header_subset(rec.get('headers') or {})}"
        )
        auth = (rec.get("headers") or {}).get("Authorization") or (rec.get("headers") or {}).get(
            "authorization"
        )
        print(f"  authorization={auth!r}")


if __name__ == "__main__":
    main()
