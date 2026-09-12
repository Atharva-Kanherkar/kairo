#!/usr/bin/env python3
"""076: same upstream safety turn, stream vs non-stream, on /anthropic/v1/messages.

Claim: the transport decides the safety verdict. The mock returns the SAME
Responses object (status completed + incomplete_details content_filter, frozen
by the 034 rig) whether Bifrost asks with stream=true or false. A lossless
pipe reports the same stop_reason both ways. If non-stream says end_turn (or
an invalid verbatim) while stream says refusal, an eval built in one mode
invalidates qualification in the other.

Cells (N=5 each, offline, no keys):
  A anthropic non-stream filter -> expect refusal (034 conformant value)
  B anthropic stream filter     -> expect refusal
  C anthropic non-stream plain  -> expect end_turn (control)
  D anthropic stream plain      -> expect end_turn (control)
  E openai non-stream filter    -> expect content_filter (control)

Divergence = A != B while C == D. Either A or B violating is a safety bug on
its own; disagreeing with each other is the transport-consistency bug.

Usage:
    python3 mock_upstream.py &
    <bifrost 1.8.6> -app-dir . -port 8080 &
    python3 hunt.py
"""
import json
import os
import sys
import urllib.request

GW = os.environ.get("GW", "http://localhost:8080")
MODEL = os.environ.get("MODEL", "mockoai/mimo-v2.5")
N = int(os.environ.get("N", "5"))
ANT = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
JSON = {"content-type": "application/json"}


def post(path, payload, headers):
    req = urllib.request.Request(GW + path, data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
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


def ant_body(marker, stream):
    return {"model": MODEL, "max_tokens": 100, "stream": stream,
            "messages": [{"role": "user", "content": marker + " hi"}]}


def cell_ant_nonstream(marker):
    d = json.loads(post("/anthropic/v1/messages", ant_body(marker, False), ANT))
    return d.get("stop_reason"), json.dumps(d, indent=2)


def cell_ant_stream(marker):
    raw = post("/anthropic/v1/messages", ant_body(marker, True), ANT)
    evs = sse_events(raw)
    reason = next((e["delta"].get("stop_reason") for e in evs
                   if e.get("type") == "message_delta" and "delta" in e), None)
    return reason, raw


def cell_oai_nonstream_filter():
    body = {"model": MODEL, "max_tokens": 100,
            "messages": [{"role": "user", "content": "SCENARIO_CONTENT_FILTER hi"}]}
    d = json.loads(post("/v1/chat/completions", body, JSON))
    return d["choices"][0].get("finish_reason"), json.dumps(d, indent=2)


CELLS = [
    ("A", "/anthropic/v1/messages", "non-stream", "filter", "refusal", lambda: cell_ant_nonstream("SCENARIO_CONTENT_FILTER")),
    ("B", "/anthropic/v1/messages", "stream", "filter", "refusal", lambda: cell_ant_stream("SCENARIO_CONTENT_FILTER")),
    ("C", "/anthropic/v1/messages", "non-stream", "plain", "end_turn", lambda: cell_ant_nonstream("SCENARIO_PLAIN_TEXT")),
    ("D", "/anthropic/v1/messages", "stream", "plain", "end_turn", lambda: cell_ant_stream("SCENARIO_PLAIN_TEXT")),
    ("E", "/v1/chat/completions", "non-stream", "filter", "content_filter", cell_oai_nonstream_filter),
]

FILES = {
    "A": "filter-nonstream.json",
    "B": "filter-stream.sse",
    "C": "plain-nonstream.json",
    "D": "plain-stream.sse",
    "E": "control-openai-filter.json",
}


def main():
    try:
        with urllib.request.urlopen(GW + "/api/version", timeout=10) as r:
            version = r.read().decode().strip()
    except Exception as e:
        print("cannot reach gateway at %s: %s" % (GW, e), file=sys.stderr)
        return 1

    print("bifrost %s, %d iterations per cell\n" % (version, N))
    print("%-4s %-24s %-11s %-11s %-11s %s" % ("ID", "ROUTE", "MODE", "EXPECTED", "OBSERVED", "N/N"))
    results = {"gateway": GW, "version": version, "model": MODEL, "n": N, "cells": []}
    seen_by_id = {}
    for cid, route, mode, scen, expected, fn in CELLS:
        seen = []
        first_raw = None
        for i in range(N):
            reason, raw = fn()
            seen.append(reason)
            if i == 0:
                first_raw = raw
        agree = sum(1 for s in seen if s == expected)
        stable = all(s == seen[0] for s in seen)
        seen_by_id[cid] = seen[0]
        print("%-4s %-24s %-11s %-11s %-11s %d/%d %s"
              % (cid, route, mode, expected, seen[0], agree, N,
                 "" if stable else "UNSTABLE"))
        results["cells"].append({"id": cid, "route": route, "mode": mode,
                                 "scenario": scen, "expected": expected,
                                 "observed": seen, "agree": agree, "of": N,
                                 "stable": stable})
        with open(FILES[cid], "w") as f:
            f.write(first_raw)

    with open("results.json", "w") as f:
        json.dump(results, f, indent=2)

    div = seen_by_id["A"] != seen_by_id["B"]
    ctrl_ok = seen_by_id["C"] == "end_turn" and seen_by_id["D"] == "end_turn"
    print("\nfilter non-stream (A): %r, filter stream (B): %r" % (seen_by_id["A"], seen_by_id["B"]))
    print("plain controls C/D: %r / %r" % (seen_by_id["C"], seen_by_id["D"]))
    if div and ctrl_ok:
        print("DIVERGENCE: same upstream safety turn, transport decides the verdict")
    elif not div:
        print("no transport divergence on this build")
    else:
        print("controls failed, rig needs attention")
    bad = [c for c in results["cells"] if c["agree"] != N]
    print("%d/%d cells conformant" % (len(results["cells"]) - len(bad), len(results["cells"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
