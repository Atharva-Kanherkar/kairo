#!/usr/bin/env python3
"""Finding 101 reproduction.

One client dialect (OpenAI Responses) against three Switchyard routes. The
variable is the target dialect of the route, and whether the declared tool has
a neutral function representation.

For every case the script records, per run, the exact bytes Switchyard forwards
upstream, then prints an N-of-N summary.

Bug cell      : a Responses host tool (no `name`, no `id`) is declared, the
                client gets HTTP 2xx, and the forwarded body has lost it.
Control cell A: the byte-identical client request on the same-dialect route
                keeps the tool.
Control cell B: a tool that does have a function representation survives the
                same cross-format route, so the hop itself is not lossy.

Output goes to the directory given as argv[1], which run_repro.sh points at a
scratch directory. The committed capture files are the frozen record and are
never rewritten by a review run.
"""
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:19412/v1/responses"
RUNS = 5

MCP = {"type": "mcp", "server_label": "srv-canary", "server_url": "https://example.invalid/mcp",
       "require_approval": "never"}
WEB_SEARCH = {"type": "web_search_preview"}
FILE_SEARCH = {"type": "file_search", "vector_store_ids": ["vs_canary"]}
CODE_INTERPRETER = {"type": "code_interpreter", "container": {"type": "auto"}}
COMPUTER_USE = {"type": "computer_use_preview", "display_width": 1024,
                "display_height": 768, "environment": "browser"}
IMAGE_GENERATION = {"type": "image_generation"}
LOCAL_SHELL = {"type": "local_shell"}
FUNCTION = {"type": "function", "name": "read_file",
            "description": "Read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                           "required": ["path"]}}
MCP_WITH_ID = {"type": "mcp", "id": "codex-tool-x1",
               "server_url": "https://example.invalid/mcp"}

CASES = [
    # name, route, tools, stream, expectation
    ("bug-mcp-chat", "model-chat", [MCP], False, "dropped"),
    ("bug-web-search-chat", "model-chat", [WEB_SEARCH], False, "dropped"),
    ("bug-file-search-chat", "model-chat", [FILE_SEARCH], False, "dropped"),
    ("bug-code-interpreter-chat", "model-chat", [CODE_INTERPRETER], False, "dropped"),
    ("bug-computer-use-chat", "model-chat", [COMPUTER_USE], False, "dropped"),
    ("bug-image-generation-chat", "model-chat", [IMAGE_GENERATION], False, "dropped"),
    ("bug-local-shell-chat", "model-chat", [LOCAL_SHELL], False, "dropped"),
    ("bug-mcp-anth", "model-anth", [MCP], False, "dropped"),
    ("bug-mcp-chat-stream", "model-chat", [MCP], True, "dropped"),
    # Byte-identical client request, same-dialect route.
    ("control-same-dialect-mcp", "model-resp", [MCP], False, "kept"),
    ("control-same-dialect-computer-use", "model-resp", [COMPUTER_USE], False, "kept"),
    ("control-same-dialect-image-generation", "model-resp", [IMAGE_GENERATION], False, "kept"),
    ("control-same-dialect-web-search", "model-resp", [WEB_SEARCH], False, "kept"),
    # A representable tool survives the same cross-format routes.
    ("control-function-chat", "model-chat", [FUNCTION], False, "kept"),
    ("control-function-anth", "model-anth", [FUNCTION], False, "kept"),
    # Data cell: a host tool that carries an `id` is not erased, it becomes an
    # empty function named after the id. Recorded because it shows the erasure
    # is a decode decision, not a filter on tool type.
    ("data-mcp-with-id-chat", "model-chat", [MCP_WITH_ID], False, "dropped"),
    # Data cells: a top-level field no IR rebuild could emit. Its survival on the
    # Responses-dialect route shows that route replays the original body instead
    # of running the ingress decoder, so the trigger cell above is the one that
    # isolates the decoder.
    ("data-preservation-bypass-resp", "model-resp", [MCP], False, "kept",
     {"zz_unknown_field": "keepme"}),
    ("data-preservation-bypass-chat", "model-chat", [MCP], False, "dropped",
     {"zz_unknown_field": "keepme"}),
]

OUT_DIR = sys.argv[1]
UPSTREAM = f"{OUT_DIR}/.upstream.jsonl"


def post_bytes(body):
    request = urllib.request.Request(BASE, data=body,
                                     headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def forwarded_record():
    with open(UPSTREAM) as handle:
        lines = [json.loads(line) for line in handle if line.strip()]
    return lines[-1] if lines else None


def forwarded_tools(record):
    if record is None:
        return None
    try:
        return json.loads(record["raw"]).get("tools")
    except Exception:
        return None


def identity(tool):
    """Tokens that prove the tool reached the target in some representation.

    A function tool is recognized by its name in any dialect. A Responses host
    tool (mcp, web_search_preview, file_search, ...) has no function shape, so
    its `type` and its server coordinates must survive verbatim.
    """
    tokens = []
    if tool.get("type") in ("function", "custom"):
        tokens.append(tool["name"])
    else:
        tokens.append(tool["type"])
        tokens.append(tool.get("server_label"))
        tokens.append(tool.get("server_url"))
    return [token for token in tokens if token]


summary = []
for case in CASES:
    name, model, tools, stream, expectation = case[:5]
    extra = case[5] if len(case) > 5 else {}
    kept = 0
    successes = 0
    for run in range(RUNS):
        open(UPSTREAM, "w").close()
        request_body = json.dumps({"model": model, "input": "CANARY_PROMPT", "tools": tools,
                                   **({"stream": True} if stream else {}), **extra}).encode()
        status, raw = post_bytes(request_body)
        record = forwarded_record()
        upstream_tools = forwarded_tools(record)
        blob = json.dumps(upstream_tools)
        erased = [tool["type"] for tool in tools
                  if upstream_tools is None
                  or not all(token in blob for token in identity(tool))]
        if 200 <= status < 300:
            successes += 1
        if not erased:
            kept += 1
        with open(f"{OUT_DIR}/capture-{name}.jsonl", "a") as handle:
            handle.write(json.dumps({
                "case": name, "run": run, "model": model, "stream": stream,
                "expectation": expectation,
                "client_status": status,
                "client_request": request_body.decode("utf-8", "replace"),
                "client_response": raw.decode("utf-8", "replace"),
                "forwarded_path": record["path"] if record else None,
                "forwarded_headers": record["headers"] if record else None,
                "forwarded_raw": record["raw"] if record else None,
                "forwarded_tools": upstream_tools,
                "erased_tools": erased,
            }) + "\n")
    summary.append({"case": name, "expectation": expectation, "runs": RUNS,
                    "http_2xx": successes, "tool_kept": kept})

print(json.dumps(summary, indent=2))
bug_rows = [row for row in summary if row["expectation"] == "dropped"]
control_rows = [row for row in summary if row["expectation"] == "kept"]
bug_ok = all(row["http_2xx"] == RUNS and row["tool_kept"] == 0 for row in bug_rows)
control_ok = all(row["tool_kept"] == RUNS for row in control_rows)
print(f"VERDICT: {'REPRODUCED' if bug_ok and control_ok else 'INCONCLUSIVE'}",
      f"({len(bug_rows)} bug cells at 0/{RUNS} kept with {RUNS}/{RUNS} HTTP 2xx, "
      f"{len(control_rows)} control cells at {RUNS}/{RUNS} kept)")