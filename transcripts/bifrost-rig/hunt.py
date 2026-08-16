#!/usr/bin/env python3
"""Bifrost translation sweep: issues 031-037, N iterations each, with controls.

Every probe runs against a local Bifrost gateway whose only provider is the
capture upstream in this directory, so nothing here needs a provider key and
every result is deterministic and replayable.

Two directions are tested:

  REQUEST  what the client sent vs what Bifrost forwarded upstream
           (capture.jsonl is ground truth: a field absent there was dropped)
  RESPONSE what the upstream returned vs what the client received

Each finding carries a control that is expected to pass, because a loss only
means something when the same gateway demonstrably preserves the same class of
information somewhere else.

Usage:
    python3 capture_upstream.py &
    npx -y @maximhq/bifrost -app-dir . -port 8080 &
    python3 hunt.py            # writes results.json and ../0NN/ evidence files
"""
import json
import os
import sys
import urllib.error
import urllib.request

GW = os.environ.get("GW", "http://localhost:8080")
MODEL = os.environ.get("MODEL", "mockoai/mimo-v2.5")
N = int(os.environ.get("N", "5"))
CAPTURE = os.environ.get("CAPTURE", "capture.jsonl")
OUT = os.environ.get("OUT", "..")
ANT = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
TOOL_ANT = [{"name": "get_time", "description": "Returns the current time.",
             "input_schema": {"type": "object", "properties": {}}}]
NASTY_ID = "call/with+punct=and.dots:1"


def post(path, payload, headers=None):
    req = urllib.request.Request(GW + path, data=json.dumps(payload).encode(),
                                 headers=headers or {"content-type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def cap_reset():
    open(CAPTURE, "w").close()


LAST_PATH = {"path": ""}


def upstream_body():
    lines = [json.loads(l) for l in open(CAPTURE) if l.strip()]
    if not lines:
        return None
    LAST_PATH["path"] = lines[0]["path"]
    return json.loads(lines[0]["body"])


def upstream_response():
    """What the upstream actually replied, so response-side findings can freeze both
    halves of the exchange rather than only what the client received."""
    lines = [json.loads(l) for l in open(CAPTURE) if l.strip()]
    return lines[0].get("response") if lines else None


def write(rel, obj):
    # A ("capture", body) pair carries its own path, recorded when the body was read.
    cap_path = LAST_PATH["path"]
    if isinstance(obj, tuple) and obj and obj[0] == "capture":
        obj = obj[1]
    path = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        if rel.endswith(".jsonl"):
            # kairo capture format: one {"path","body"} line, body as an object, so
            # the existing capture-rig checkers read these fixtures unchanged.
            f.write(json.dumps({"path": cap_path, "body": obj}) + "\n")
        else:
            f.write(obj if isinstance(obj, str) else json.dumps(obj, indent=2) + "\n")


# ---------------------------------------------------------------- probes ----
# Each returns (observed_value, evidence_object). The runner compares observed
# against `expected` N times.

def p031_parallel():
    cap_reset()
    post("/anthropic/v1/messages", {
        "model": MODEL, "max_tokens": 100, "tools": TOOL_ANT,
        "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
        "messages": [{"role": "user", "content": "time?"}]}, ANT)
    b = upstream_body()
    return b.get("parallel_tool_calls", "<absent>"), b


def p031_control():
    cap_reset()
    post("/v1/chat/completions", {
        "model": MODEL, "max_tokens": 100, "parallel_tool_calls": False,
        "tools": [{"type": "function", "function": {
            "name": "get_time", "description": "t",
            "parameters": {"type": "object", "properties": {}}}}],
        "messages": [{"role": "user", "content": "time?"}]})
    b = upstream_body()
    return b.get("parallel_tool_calls", "<absent>"), b


def p032_stop():
    cap_reset()
    post("/anthropic/v1/messages", {
        "model": MODEL, "max_tokens": 100, "stop_sequences": ["STOPPROBE"],
        "messages": [{"role": "user", "content": "hi"}]}, ANT)
    b = upstream_body()
    return ("STOPPROBE" in json.dumps(b)), b


def p032_control():
    cap_reset()
    post("/v1/chat/completions", {
        "model": MODEL, "max_tokens": 100, "stop": ["STOPPROBE"],
        "messages": [{"role": "user", "content": "hi"}]})
    b = upstream_body()
    return ("STOPPROBE" in json.dumps(b)), b


def p033_thinking():
    cap_reset()
    post("/anthropic/v1/messages", {"model": MODEL, "max_tokens": 100, "messages": [
        {"role": "user", "content": "2+2?"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "THINKPROBE simple arithmetic",
             "signature": "sigabc"},
            {"type": "text", "text": "4"}]},
        {"role": "user", "content": "and 3+3?"}]}, ANT)
    b = upstream_body()
    return ("THINKPROBE" in json.dumps(b)), b


def p033_control():
    """is_error survives the same translator, so dropping is not inherent to it."""
    cap_reset()
    post("/anthropic/v1/messages", {"model": MODEL, "max_tokens": 100, "messages": [
        {"role": "user", "content": "run"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "get_time", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "boom",
             "is_error": True}]}]}, ANT)
    b = upstream_body()
    item = next((i for i in b["input"] if i.get("type") == "function_call_output"), {})
    return item.get("status"), b


def _ant_scenario(marker):
    cap_reset()
    _, r = post("/anthropic/v1/messages", {
        "model": MODEL, "max_tokens": 100,
        "messages": [{"role": "user", "content": marker + " hi"}]}, ANT)
    return json.loads(r)


def p034_content_filter():
    d = _ant_scenario("SCENARIO_CONTENT_FILTER")
    return d.get("stop_reason"), d


def p034_control():
    cap_reset()
    _, r = post("/v1/chat/completions", {
        "model": MODEL, "max_tokens": 100,
        "messages": [{"role": "user", "content": "SCENARIO_CONTENT_FILTER hi"}]})
    d = json.loads(r)
    return d["choices"][0].get("finish_reason"), d


def p035_truncation():
    d = _ant_scenario("SCENARIO_LENGTH")
    return d.get("stop_reason"), {
        "client_response": d,
        "_extra_files": {"035/upstream-response.json": upstream_response()}}


def p035_control():
    """A complete, untruncated, text-only turn. The client is told `end_turn` and
    that is CORRECT, so the checker must call this conformant. It is the control
    that proves the invariant distinguishes a truncated turn from a finished one
    rather than flagging every `end_turn`."""
    d = _ant_scenario("SCENARIO_PLAIN_TEXT")
    return d.get("stop_reason"), {
        "client_response": d,
        "_extra_files": {"035/control-upstream-response.json": upstream_response()}}


def p036_refusal():
    d = _ant_scenario("SCENARIO_REFUSAL")
    return [b.get("type") for b in d.get("content", [])], d


def p036_control():
    """A plain text turn keeps its content, so empty content is not the norm."""
    cap_reset()
    _, r = post("/anthropic/v1/messages", {
        "model": MODEL, "max_tokens": 100,
        "messages": [{"role": "user", "content": "plain hi"}]}, ANT)
    d = json.loads(r)
    return [b.get("type") for b in d.get("content", [])], d


def p037_toolid_roundtrip():
    """Turn 1 sanitizes the id; turn 2 must restore it for the upstream."""
    cap_reset()
    _, r = post("/anthropic/v1/messages", {
        "model": MODEL, "max_tokens": 100, "tools": TOOL_ANT,
        "messages": [{"role": "user", "content": "SCENARIO_NASTY_TOOLID time?"}]}, ANT)
    d = json.loads(r)
    tu = next((b for b in d.get("content", []) if b.get("type") == "tool_use"), {})
    client_id = tu.get("id")
    cap_reset()
    post("/anthropic/v1/messages", {
        "model": MODEL, "max_tokens": 100, "tools": TOOL_ANT,
        "messages": [
            {"role": "user", "content": "time?"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": client_id, "name": "get_time", "input": {}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": client_id, "content": "12:00"}]}]},
        ANT)
    b = upstream_body()
    sent = [i.get("call_id") for i in b.get("input", []) if "call_id" in i]
    restored = NASTY_ID in sent
    # The turn-2 request is emitted through _extra_files so the runner writes it on
    # the same iteration it writes roundtrip.json. Writing it here would rewrite it
    # every run, leaving the two committed witnesses describing different turns.
    return restored, {"_extra_files": {"037/upstream-request-turn2.jsonl": ("capture", b)},
                      "upstream_original_id": NASTY_ID, "client_received_id": client_id,
                      "ids_sent_upstream_turn2": sent, "upstream_request_turn2": b}


# (id, title, fn, conformant_value, evidence_path, field)
# `conformant_value` is always what a lossless pipe MUST produce, never the
# observed defect, so the N/N column reads the same way on every row: a control
# scores N/N and a violation scores 0/N.
PROBES = [
    ("031", "disable_parallel_tool_use reaches upstream", p031_parallel, False,
     "031/upstream-request.jsonl", "parallel_tool_calls == false"),
    ("031c", "control: OpenAI route forwards parallel_tool_calls", p031_control, False,
     "031/control-openai-upstream.jsonl", "parallel_tool_calls == false"),
    ("032", "stop_sequences reaches upstream", p032_stop, True,
     "032/upstream-request.jsonl", "STOPPROBE present upstream"),
    ("032c", "control: OpenAI route forwards stop", p032_control, True,
     "032/control-openai-upstream.jsonl", "STOPPROBE present upstream"),
    ("033", "thinking history reaches upstream", p033_thinking, True,
     "033/upstream-request.jsonl", "THINKPROBE present upstream"),
    ("033c", "control: is_error survives the same translator", p033_control, "incomplete",
     "033/control-is-error-upstream.jsonl", "function_call_output.status"),
    ("034", "content_filter preserved to the client", p034_content_filter, "refusal",
     "034/anthropic-response.json", "Anthropic stop_reason"),
    ("034c", "control: OpenAI route keeps content_filter", p034_control, "content_filter",
     "034/control-openai-response.json", "OpenAI finish_reason"),
    ("035", "truncation preserved to the client", p035_truncation, "max_tokens",
     "035/anthropic-response.json", "Anthropic stop_reason"),
    ("035c", "control: a turn the upstream did not truncate", p035_control, "end_turn",
     "035/control-anthropic-response.json", "Anthropic stop_reason"),
    ("036", "refusal content preserved", p036_refusal, ["text"],
     "036/anthropic-response.json", "Anthropic content block types"),
    ("036c", "control: a plain turn keeps its content", p036_control, ["text", "tool_use"],
     "036/control-plain-response.json", "Anthropic content block types"),
    ("037", "sanitized tool id restored for upstream", p037_toolid_roundtrip, True,
     "037/roundtrip.json", "original upstream id restored"),
]


def main():
    try:
        with urllib.request.urlopen(GW + "/api/version", timeout=10) as r:
            version = r.read().decode().strip()
    except Exception as e:  # noqa: BLE001
        print("cannot reach gateway at %s: %s" % (GW, e), file=sys.stderr)
        return 1

    print("bifrost %s, %d iterations per probe\n" % (version, N))
    print("%-5s %-46s %-22s %s" % ("ID", "PROBE", "OBSERVED", "CONFORMANT N/N"))
    results = {"version": version, "model": MODEL, "n": N, "probes": []}

    for pid, title, fn, expected, evidence_path, field in PROBES:
        seen, evidence, extras = [], None, {}
        for i in range(N):
            got, ev = fn()
            seen.append(got)
            if i == 0:
                # Freeze every witness for a probe from the SAME iteration, so two
                # fixtures for one finding can never describe two different turns.
                if isinstance(ev, dict) and "_extra_files" in ev:
                    ev = dict(ev)
                    extras = ev.pop("_extra_files")
                    if set(ev.keys()) == {"client_response"}:
                        ev = ev["client_response"]
                evidence = ev
        agree = sum(1 for s in seen if s == expected)
        stable = all(s == seen[0] for s in seen)
        print("%-5s %-46s %-22s %d/%d %s"
              % (pid, title, repr(seen[0])[:22], agree, N,
                 "" if stable else "  UNSTABLE"))
        results["probes"].append({
            "id": pid, "title": title, "field": field, "conformant_value": expected,
            "observed": seen, "matches_expected": agree, "of": N, "stable": stable})
        if evidence is not None:
            write(evidence_path, evidence)
        for rel, obj in extras.items():
            write(rel, obj)

    write("bifrost-rig/results.json", results)
    unstable = [p for p in results["probes"] if not p["stable"]]
    print("\n%d/%d probes deterministic across %d runs"
          % (len(results["probes"]) - len(unstable), len(results["probes"]), N))
    for p in unstable:
        print("  UNSTABLE %s: %s" % (p["id"], p["observed"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
