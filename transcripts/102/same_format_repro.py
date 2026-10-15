#!/usr/bin/env python3
"""Same claim on a same-format Responses to Responses passthrough route.

Mirrors `repro.py` for the cross-dialect case. Records land in
`capture-same-format-*.jsonl` so the harness checker can read them unchanged.
"""

import http.client
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
HOST = "127.0.0.1:19332"
FORWARDED = ROOT / "forwarded-same-format.jsonl"
NAMESPACE = "mcp__docs__"
QUALIFIED = NAMESPACE + "__search"

LOCAL_TOOL = {
    "type": "function",
    "name": "search",
    "description": "LOCAL ripgrep over the working tree",
    "parameters": {"type": "object", "properties": {"q": {"type": "string"}},
                   "required": ["q"]},
}
NAMESPACE_CONTAINER = {
    "type": "namespace",
    "name": NAMESPACE,
    "tools": [{
        "type": "function",
        "name": "search",
        "description": "REMOTE documentation search",
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}},
                       "required": ["q"]},
    }],
}
MODES = {
    "bug": {"canary": "PICK_LOCAL", "tools": [LOCAL_TOOL, NAMESPACE_CONTAINER]},
    "cross-qualified": {"canary": "PICK_QUALIFIED", "tools": [LOCAL_TOOL, NAMESPACE_CONTAINER]},
    "control-local-only": {"canary": "PICK_LOCAL", "tools": [LOCAL_TOOL]},
}


def send(body):
    connection = http.client.HTTPConnection(HOST)
    connection.request("POST", "/v1/responses", body=json.dumps(body, separators=(",", ":")),
                       headers={"content-type": "application/json"})
    response = connection.getresponse()
    raw = response.read().decode()
    connection.close()
    if response.status != 200:
        raise RuntimeError(f"Switchyard returned {response.status}: {raw}")
    return raw


def forwarded_call_for(canary):
    """The upstream call for this canary. A canary must match exactly one record."""
    matches = [
        json.loads(line)
        for line in FORWARDED.read_text().splitlines()
        if canary in json.loads(line)["body_raw"]
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"canary {canary!r} matched {len(matches)} forwarded records, expected 1")
    return matches[0]["called_upstream_name"], matches[0]["tool_names_offered_upstream"]


def call_items(raw):
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

    visit(json.loads(raw))
    return items


def run(mode, trial):
    spec = MODES[mode]
    canary = f"SAMEFORMAT_CANARY_095_{mode}_{trial}"
    body = {"model": "switchyard/responses", "input": f"{spec['canary']} {canary}",
            "tools": spec["tools"], "tool_choice": "required", "stream": False}
    raw = send(body)
    calls = call_items(raw)
    if not calls:
        raise RuntimeError(f"no function_call in {mode}: {raw}")
    called, offered = forwarded_call_for(canary)
    return {
        "trial": trial,
        "mode": mode,
        "streaming": False,
        "called_upstream_name": called,
        "tool_names_offered_upstream": offered,
        "client_request_body_raw": json.dumps(body, separators=(",", ":")),
        "client_response_body_raw": raw,
        "call_names": sorted({c.get("name") for c in calls}),
        "call_namespaces": sorted({c.get("namespace") for c in calls if c.get("namespace")}),
    }


def main():
    results = {}
    for mode in MODES:
        records = [run(mode, trial) for trial in range(1, 6)]
        (ROOT / f"capture-same-format-{mode}.jsonl").write_text(
            "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records))
        hijacked = sum(1 for r in records
                       if r["called_upstream_name"] == "search"
                       and r["call_names"] == ["search"]
                       and r["call_namespaces"] == [NAMESPACE])
        clean = sum(1 for r in records if r["call_namespaces"] == [])
        called = sorted({r["called_upstream_name"] for r in records})
        results[mode] = {"hijacked": hijacked, "clean": clean, "called": called}
        print(f"same-format {mode:20s} hijacked={hijacked}/5 clean={clean}/5 called={called}")

    if results["bug"]["hijacked"] != 5:
        raise SystemExit(f"same-format bug did not reproduce: {results['bug']}")
    if results["bug"]["called"] != ["search"]:
        raise SystemExit(f"same-format bug upstream call wrong: {results['bug']}")
    if results["cross-qualified"]["hijacked"] != 0:
        raise SystemExit("same-format cross-qualified must not be a hijack")
    if results["control-local-only"]["clean"] != 5:
        raise SystemExit("same-format local-only control misrouted")
    print(json.dumps({"result": "reproduced", "same_format": results}))


if __name__ == "__main__":
    main()