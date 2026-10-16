#!/usr/bin/env python3
"""Reproduce a Codex tool namespace being stamped onto an un-namespaced tool.

The client sends OpenAI Responses to Switchyard, which translates to a Chat
Completions backend. The backend only ever calls a tool name the gateway
actually offered it, and never emits a `namespace` field, so any namespace that
reaches the client was added by the gateway's response rewrite.

Four cases. The tool set and the name the backend calls are the only variables.

  bug                top-level `search` AND namespace `mcp__docs__` holding its
                     own `search`; backend calls the bare `search`, which is the
                     top-level tool. A namespace must not appear.
  cross-qualified   the SAME tool set, backend calls the qualified upstream name.
                     Restoring the pair is correct here, and must not be flagged.
                     This is the case that separates a hijack from a valid
                     restore.
  control-qualified  namespace only; backend calls the qualified name.
  control-local-only top-level `search` only; backend calls the bare name.

Both a buffered and a streamed request are exercised.
"""

import http.client
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
HOST = "127.0.0.1:19322"
FORWARDED = ROOT / "forwarded.jsonl"

# Real Codex spells an MCP container "mcp__<server>__" with the trailing
# separator, so the flattened upstream name carries four underscores.
NAMESPACE = "mcp__docs__"

LOCAL_TOOL = {
    "type": "function",
    "name": "search",
    "description": "LOCAL ripgrep over the working tree",
    "parameters": {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
    },
}
NAMESPACED_TOOL = {
    "type": "function",
    "name": "search",
    "description": "REMOTE documentation search",
    "parameters": {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
    },
}
NAMESPACE_CONTAINER = {
    "type": "namespace",
    "name": NAMESPACE,
    "tools": [NAMESPACED_TOOL],
}

BOTH_SHAPES = [LOCAL_TOOL, NAMESPACE_CONTAINER]

MODES = {
    "bug": {"canary": "PICK_LOCAL", "tools": BOTH_SHAPES},
    "cross-qualified": {"canary": "PICK_QUALIFIED", "tools": BOTH_SHAPES},
    "control-qualified": {"canary": "PICK_QUALIFIED", "tools": [NAMESPACE_CONTAINER]},
    "control-local-only": {"canary": "PICK_LOCAL", "tools": [LOCAL_TOOL]},
}

# Modes where the model chose the top-level tool, so any namespace is a hijack.
LOCAL_EXPECTED = {"bug", "control-local-only"}


def send(body, stream):
    headers = {"content-type": "application/json"}
    if stream:
        headers["accept"] = "text/event-stream"
    connection = http.client.HTTPConnection(HOST)
    connection.request("POST", "/v1/responses", body=json.dumps(body, separators=(",", ":")),
                       headers=headers)
    response = connection.getresponse()
    raw = response.read().decode()
    connection.close()
    if response.status != 200:
        raise RuntimeError(f"Switchyard returned {response.status}: {raw}")
    return raw


def forwarded_call_for(canary):
    """The name the backend was recorded calling for this trial's canary.

    A canary must match exactly one forwarded record. A loose match could pair a
    response with a different request's upstream call.
    """
    matches = []
    for line in FORWARDED.read_text().splitlines():
        record = json.loads(line)
        if canary in record["body_raw"]:
            matches.append(record)
    if len(matches) != 1:
        raise RuntimeError(
            f"canary {canary!r} matched {len(matches)} forwarded records, expected 1")
    return matches[0]["called_upstream_name"], matches[0]["tool_names_offered_upstream"]


def call_items(raw, stream):
    """Pull every function_call-shaped object out of a buffered body or SSE stream."""
    items = []

    def visit(value):
        if isinstance(value, dict):
            if value.get("type") == "function_call" or (
                value.get("type") == "response.output_item.added"
                and isinstance(value.get("item"), dict)
                and value["item"].get("type") == "function_call"
            ):
                items.append(value.get("item", value))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    if stream:
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            visit(json.loads(payload))
    else:
        visit(json.loads(raw))
    return items


def run(mode, stream, trial):
    spec = MODES[mode]
    # The canary has to be unique per request, not per case: the buffered and
    # streamed runs of the same mode and trial are separate forwarded records.
    kind = "stream" if stream else "buffered"
    canary = f"NAMESPACE_CANARY_095_{mode}_{kind}_{trial}"
    body = {
        "model": "switchyard/chat",
        "input": f"{spec['canary']} {canary}",
        "tools": spec["tools"],
        "tool_choice": "required",
        "stream": stream,
    }
    raw = send(body, stream)
    calls = call_items(raw, stream)
    if not calls:
        raise RuntimeError(f"no function_call in {mode}: {raw}")
    called_upstream_name, offered = forwarded_call_for(canary)
    return {
        "trial": trial,
        "mode": mode,
        "streaming": stream,
        "called_upstream_name": called_upstream_name,
        "tool_names_offered_upstream": offered,
        "client_request_body_raw": json.dumps(body, separators=(",", ":")),
        "client_response_body_raw": raw,
        "call_names": sorted({call.get("name") for call in calls}),
        "call_namespaces": sorted({call.get("namespace") for call in calls
                                   if call.get("namespace")}),
    }


def main():
    counts = {}
    for mode in MODES:
        for stream in (False, True):
            records = [run(mode, stream, trial) for trial in range(1, 6)]
            name = f"capture-{mode}-{'stream' if stream else 'buffered'}.jsonl"
            (ROOT / name).write_text(
                "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records))
            namespaced = sum(1 for r in records
                             if r["call_names"] == ["search"]
                             and r["call_namespaces"] == [NAMESPACE])
            bare = sum(1 for r in records
                       if r["call_names"] == ["search"] and r["call_namespaces"] == [])
            called = sorted({r["called_upstream_name"] for r in records})
            key = f"{mode}/{'stream' if stream else 'buffered'}"
            counts[key] = {"namespaced": namespaced, "bare": bare,
                           "called": called, "trials": len(records)}
            print(f"{mode:20s} {'stream' if stream else 'buffered':8s} "
                  f"namespaced={namespaced}/{len(records)} bare={bare}/{len(records)} "
                  f"upstream_called={called}")

    # `cross-qualified` and `bug` return the SAME client bytes. The only thing
    # that separates a valid restore from a hijack is the name the backend was
    # recorded calling, which is why the checker reads the forwarded record.
    qualified = NAMESPACE + "__search"
    expected = {
        # mode: (namespaced per trial, the upstream name every trial called)
        "bug": (5, "search"),
        "cross-qualified": (5, qualified),
        "control-qualified": (5, qualified),
        "control-local-only": (0, "search"),
    }
    for streaming in (False, True):
        for mode, (want_namespaced, want_called) in expected.items():
            key = f"{mode}/{'stream' if streaming else 'buffered'}"
            got = counts[key]
            if got["namespaced"] != want_namespaced:
                raise SystemExit(
                    f"{key}: expected {want_namespaced}/5 namespaced, got {got['namespaced']}")
            if got["called"] != [want_called]:
                raise SystemExit(
                    f"{key}: expected upstream call {want_called!r}, got {got['called']}")
    print(json.dumps({"result": "reproduced", "counts": counts}))


if __name__ == "__main__":
    main()