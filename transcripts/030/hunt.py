#!/usr/bin/env python3
"""030: Bifrost /anthropic/v1/messages streaming loses stop_reason=tool_use.

Runs the four-cell control matrix N times against a local Bifrost gateway whose
upstream is the mock OpenAI-compatible server in this directory. No provider keys
are involved: the mock always returns the same logical turn (one sentence of text,
then a `get_time` tool call), so every cell is deterministic and offline.

    ROUTE                     MODE        EXPECTED TERMINAL REASON
    /v1/chat/completions      non-stream  finish_reason tool_calls
    /v1/chat/completions      stream      finish_reason tool_calls
    /anthropic/v1/messages    non-stream  stop_reason   tool_use
    /anthropic/v1/messages    stream      stop_reason   tool_use   <-- the bug

Usage:
    python3 mock_upstream.py &
    npx -y @maximhq/bifrost -app-dir . -port 8080 &
    python3 hunt.py
"""
import json
import os
import sys
import urllib.request

GW = os.environ.get("GW", "http://localhost:8080")
MODEL = os.environ.get("MODEL", "mockoai/mimo-v2.5")
N = int(os.environ.get("N", "5"))

OAI_TOOLS = [{"type": "function", "function": {
    "name": "get_time", "description": "Returns the current time.",
    "parameters": {"type": "object", "properties": {}}}}]
ANT_TOOLS = [{"name": "get_time", "description": "Returns the current time.",
              "input_schema": {"type": "object", "properties": {}}}]
PROMPT = ("First write one short sentence saying you will check the time, "
          "then call the get_time tool.")


def post(path, payload, extra_headers=None):
    body = json.dumps(payload).encode()
    headers = {"content-type": "application/json"}
    headers.update(extra_headers or {})
    req = urllib.request.Request(GW + path, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def sse_events(raw):
    out = []
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        d = line[6:].strip()
        if d == "[DONE]":
            continue
        try:
            out.append(json.loads(d))
        except json.JSONDecodeError:
            pass
    return out


def oai_body(stream):
    return {"model": MODEL, "max_tokens": 400, "stream": stream,
            "tools": OAI_TOOLS, "messages": [{"role": "user", "content": PROMPT}]}


def ant_body(stream):
    return {"model": MODEL, "max_tokens": 400, "stream": stream,
            "tools": ANT_TOOLS, "messages": [{"role": "user", "content": PROMPT}]}


ANT_HDRS = {"anthropic-version": "2023-06-01"}


def cell_oai_nonstream():
    d = json.loads(post("/v1/chat/completions", oai_body(False)))
    return d["choices"][0].get("finish_reason"), None


def cell_oai_stream():
    raw = post("/v1/chat/completions", oai_body(True))
    reasons = [c["choices"][0].get("finish_reason")
               for c in sse_events(raw) if c.get("choices")]
    return next((r for r in reversed(reasons) if r), None), raw


def cell_ant_nonstream():
    d = json.loads(post("/anthropic/v1/messages", ant_body(False), ANT_HDRS))
    return d.get("stop_reason"), json.dumps(d, indent=2)


def cell_ant_stream():
    raw = post("/anthropic/v1/messages", ant_body(True), ANT_HDRS)
    evs = sse_events(raw)
    has_tool_use = any(e.get("type") == "content_block_start"
                       and (e.get("content_block") or {}).get("type") == "tool_use"
                       for e in evs)
    reason = next((e["delta"].get("stop_reason") for e in evs
                   if e.get("type") == "message_delta" and "delta" in e), None)
    return reason, raw, has_tool_use


CELLS = [
    ("/v1/chat/completions", "non-stream", "tool_calls", cell_oai_nonstream),
    ("/v1/chat/completions", "stream", "tool_calls", cell_oai_stream),
    ("/anthropic/v1/messages", "non-stream", "tool_use", cell_ant_nonstream),
    ("/anthropic/v1/messages", "stream", "tool_use", cell_ant_stream),
]


def main():
    try:
        with urllib.request.urlopen(GW + "/api/version", timeout=10) as r:
            version = r.read().decode().strip()
    except Exception as e:  # noqa: BLE001
        print("cannot reach gateway at %s: %s" % (GW, e), file=sys.stderr)
        return 1

    results = {"gateway": GW, "version": version, "model": MODEL, "n": N, "cells": []}
    saved = {}
    print("bifrost %s, %d iterations\n" % (version, N))
    print("%-24s %-11s %-11s %-11s %s" % ("ROUTE", "MODE", "EXPECTED", "OBSERVED", "PASS"))

    for route, mode, expected, fn in CELLS:
        seen, tool_use_seen = [], []
        for i in range(N):
            got = fn()
            reason, raw = got[0], got[1]
            if len(got) > 2:
                tool_use_seen.append(got[2])
            seen.append(reason)
            if raw and i == 0:
                saved[(route, mode)] = raw
        agree = sum(1 for s in seen if s == expected)
        ok = agree == N
        print("%-24s %-11s %-11s %-11s %d/%d %s"
              % (route, mode, expected, seen[0], agree, N, "PASS" if ok else "FAIL"))
        cell = {"route": route, "mode": mode, "expected": expected,
                "observed": seen, "agree": agree, "of": N, "pass": ok}
        if tool_use_seen:
            cell["tool_use_block_present"] = tool_use_seen
        results["cells"].append(cell)

    with open("results.json", "w") as f:
        json.dump(results, f, indent=2)
    for (route, mode), raw in saved.items():
        name = ("anthropic" if "anthropic" in route else "openai") + \
               ("-stream" if mode == "stream" else "-nonstream")
        ext = "sse" if mode == "stream" else "json"
        with open("%s.%s" % (name, ext), "w") as f:
            f.write(raw)

    bad = [c for c in results["cells"] if not c["pass"]]
    print("\n%d/%d cells conformant" % (len(results["cells"]) - len(bad), len(results["cells"])))
    for c in bad:
        print("  VIOLATION %s %s: expected %r, observed %r"
              % (c["route"], c["mode"], c["expected"], c["observed"][0]))
        if c.get("tool_use_block_present"):
            print("            tool_use block present in stream: %s"
                  % all(c["tool_use_block_present"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
