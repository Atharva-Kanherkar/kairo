#!/usr/bin/env python3
"""Show which tool the client would actually run, using Codex's dispatch rule.

Codex registers tools under a `ToolName` that is either plain or namespaced
(`ToolName::plain("update_plan")` against
`ToolName::namespaced("multi_agent_v1", "spawn_agent")`), and its registry looks
a call up by the exact `{name, namespace}` pair. A call carrying a namespace
resolves inside that namespace's container; a call without one resolves in the
top-level registry. There is no fallback from a namespaced call to a plain one.

The expected target is derived from the name the backend was recorded calling
upstream, not from a per-mode assumption, so this cannot grade itself.

Expected upstream call names, given the tool set the client sent:

  bug                 model calls bare `search`        -> top-level ripgrep
  cross-qualified     model calls `mcp__docs____search` -> MCP documentation search
  control-qualified   model calls `mcp__docs____search` -> MCP documentation search
  control-local-only  model calls bare `search`        -> top-level ripgrep
"""

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
NAMESPACE = "mcp__docs__"
QUALIFIED = NAMESPACE + "__search"

LOCAL_TOOL = ("search", "LOCAL ripgrep over the working tree")
NAMESPACED_TOOL = ("search", "REMOTE documentation search")

# Which tool each upstream name denotes. A bare `search` is the top-level tool
# because the gateway offered both under distinct names.
TARGETS = {
    "search": (None, LOCAL_TOOL[1]),
    QUALIFIED: (NAMESPACE, NAMESPACED_TOOL[1]),
}


def build_registry(mode):
    """Top-level and per-namespace tool tables, as Codex builds them."""
    plain, namespaced = {}, {}
    if mode in ("bug", "cross-qualified", "control-local-only"):
        plain[LOCAL_TOOL[0]] = LOCAL_TOOL[1]
    if mode in ("bug", "cross-qualified", "control-qualified"):
        namespaced[(NAMESPACE, NAMESPACED_TOOL[0])] = NAMESPACED_TOOL[1]
    return plain, namespaced


def dispatch(call, plain, namespaced):
    namespace = call.get("namespace")
    name = call.get("name")
    if namespace:
        return namespace, name, namespaced.get((namespace, name))
    return None, name, plain.get(name)


def calls_in(record):
    raw = record["client_response_body_raw"]
    out = []
    if record["streaming"]:
        for line in raw.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload and payload != "[DONE]":
                    value = json.loads(payload)
                    item = value.get("item", value)
                    if item.get("type") == "function_call":
                        out.append(item)
                    elif value.get("type") == "function_call":
                        out.append(value)
    else:
        out.extend(i for i in json.loads(raw)["output"] if i.get("type") == "function_call")
    return out


def main():
    verdicts = {}
    for mode in ("bug", "cross-qualified", "control-qualified", "control-local-only"):
        for streaming in (False, True):
            kind = "stream" if streaming else "buffered"
            plain, namespaced = build_registry(mode)
            rows = []
            for line in (ROOT / f"capture-{mode}-{kind}.jsonl").read_text().splitlines():
                record = json.loads(line)
                called = record["called_upstream_name"]
                want_namespace, want_description = TARGETS[called]
                for call in calls_in(record):
                    namespace, name, description = dispatch(call, plain, namespaced)
                    rows.append({
                        "trial": record["trial"],
                        "called_upstream_name": called,
                        "received_name": name,
                        "received_namespace": namespace,
                        "expected_target": want_description,
                        "dispatched_to": description,
                        "wrong": description != want_description,
                    })
            wrong_trials = len({r["trial"] for r in rows if r["wrong"]})
            trials = len({r["trial"] for r in rows})
            key = f"{mode}/{kind}"
            verdicts[key] = {"trials": trials, "calls": len(rows),
                             "dispatched_to_the_wrong_tool": wrong_trials}
            print(f"{mode:20s} {kind:8s} wrong_tool={wrong_trials}/{trials} "
                  f"expected={rows[0]['expected_target']!r} "
                  f"got={rows[0]['dispatched_to']!r}")

    # Only the hijack misroutes. Every other case lands on the chosen tool.
    for kind in ("buffered", "stream"):
        if verdicts[f"bug/{kind}"]["dispatched_to_the_wrong_tool"] != 5:
            raise SystemExit(f"bug/{kind}: expected 5 misrouted")
        for mode in ("cross-qualified", "control-qualified", "control-local-only"):
            if verdicts[f"{mode}/{kind}"]["dispatched_to_the_wrong_tool"]:
                raise SystemExit(f"{mode}/{kind}: control misrouted")
    print(json.dumps(verdicts))
    print("consumer-boundary check: the local tool is misrouted to the MCP "
          "server in 5/5 bug trials; every control lands on the chosen tool")


if __name__ == "__main__":
    main()