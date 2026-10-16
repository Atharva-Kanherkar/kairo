"""Reproduce finding 103 against locally built Bifrost HTTP gateways.

Starts each Bifrost build with rig/config.json, puts a recording relay on both
hops (client -> Bifrost on 9941, Bifrost -> api.openai.com on 9940), and writes
every exchange of every case under --output/<build>/<group>/<case>/:

  minimal/   Anthropic Messages requests that replay a tool-search turn, sent to
             /anthropic/v1/messages. `deferred-*` replays Claude Code's client
             search (ToolSearch, a tool_reference result, then a call to the
             loaded tool). `eager-*` is the same history with the tool sent
             eagerly: the trigger removed. `hosted-*` uses Anthropic's
             server-side tool_search_tool_bm25 with turn 1 generated live.
  responses/ The same history in the Responses dialect on /v1/responses, with
             and without the function_call namespace, to show what the
             provider requires when the client can carry the field.
  direct/    No gateway: the exact body Bifrost forwarded for the first
             deferred run, sent to OpenAI as recorded and with only
             `namespace` added to the replayed function_call.
  claude/    (with --claude NAME) Claude Code print mode with an MCP server, asked
             to look up an order, with ENABLE_TOOL_SEARCH=true and =false.
             Each tool-search run is then resumed once with --continue.

The OpenAI key is read from OPENAI_API_KEY or from --env-file. It is passed to
Bifrost and to the direct calls and never written: the relays redact it, and
the script scans every output file for it before exiting.
"""
import argparse
import http.client
import itertools
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RIG = pathlib.Path(__file__).resolve().parent
GATEWAY_PORT, CLIENT_RELAY_PORT, UPSTREAM_RELAY_PORT = 8094, 9941, 9940
OPENAI = "https://api.openai.com"
CANARY = "SHIPPED-CANARY-7731"
REJECTION = "Missing namespace for function_call 'mcp__orders__lookup_order'"
TOOL = "mcp__orders__lookup_order"
LOOKUP = {
    "name": TOOL,
    "description": "Look up the shipping status of a customer order by its order id.",
    "input_schema": {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
    },
}
SEARCH = {
    "name": "ToolSearch",
    "description": "Search for deferred tools and load them.",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}, "max_results": {"type": "number"}},
        "required": ["query"],
    },
}
QUESTION = "What is the shipping status of order A-1001? Use the orders tools."
CLAUDE_PROMPT = "What is the shipping status of order A-1001? Use the orders tools."

# ---------------------------------------------------------------- relays --

CREDENTIAL_HEADERS = {"x-api-key", "authorization", "cookie", "set-cookie", "api-key"}
TENANT_HEADERS = {"openai-organization", "openai-project"}
TRACE_HEADERS = {"x-request-id", "cf-ray", "x-openai-proxy-wasm", "openai-processing-ms"}
HOP = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding", "keep-alive"}
DEVICE_ID = re.compile(rb'(device_id[\\"\s:]+)[0-9a-f]{32,}')
# Bifrost echoes provider response headers inside JSON bodies on some routes.
ECHOED_HEADERS = [
    (re.compile(rb'("(?:openai-organization|openai-project)":")[^"]*"', re.I), rb'\1[REDACTED: tenant id]"'),
    (re.compile(rb'("(?:cf-ray|x-request-id|openai-processing-ms)":")[^"]*"', re.I), rb'\1[REDACTED: trace id]"'),
    (re.compile(rb'("x-ratelimit-[a-z-]+":")[^"]*"', re.I), rb'\1[REDACTED: rate limit]"'),
    (re.compile(rb'("set-cookie":")[^"]*"', re.I), rb'\1[REDACTED: credential]"'),
]


def redact_body(body):
    body = DEVICE_ID.sub(rb"\1[REDACTED: device id]", body)
    for pattern, replacement in ECHOED_HEADERS:
        body = pattern.sub(replacement, body)
    return body


def render_headers(items):
    lines = []
    for name, value in items:
        low = name.lower()
        if low in CREDENTIAL_HEADERS:
            value = "[REDACTED: credential]"
        elif low in TENANT_HEADERS:
            value = "[REDACTED: tenant id]"
        elif low in TRACE_HEADERS:
            value = "[REDACTED: trace id]"
        elif low.startswith("x-ratelimit-"):
            value = "[REDACTED: rate limit]"
        lines.append(f"{name}: {value}")
    return "\r\n".join(lines).encode()


class Relay:
    """A recording relay whose output directory can be switched between cases."""

    def __init__(self, port, upstream):
        self.upstream = urllib.parse.urlsplit(upstream)
        self.out = None
        self.counter = itertools.count(1)
        self.lock = threading.Lock()
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                relay.handle(self)

            def do_POST(self):
                relay.handle(self)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def record_into(self, out):
        with self.lock:
            self.out = out
            self.counter = itertools.count(1)
        if out:
            out.mkdir(parents=True, exist_ok=True)

    def handle(self, h):
        length = int(h.headers.get("content-length", 0) or 0)
        body = h.rfile.read(length) if length else b""
        with self.lock:
            out, n = self.out, next(self.counter)
        prefix = out / f"{n:03d}" if out else None
        if prefix:
            with open(f"{prefix}-request.http", "wb") as f:
                f.write(f"{h.command} {h.path} HTTP/1.1\r\n".encode())
                f.write(render_headers(h.headers.items()) + b"\r\n\r\n")
                f.write(redact_body(body))
        cls = http.client.HTTPSConnection if self.upstream.scheme == "https" else http.client.HTTPConnection
        conn = cls(self.upstream.hostname, self.upstream.port, timeout=600)
        headers = {k: v for k, v in h.headers.items() if k.lower() not in HOP}
        headers["host"] = self.upstream.netloc
        conn.request(h.command, self.upstream.path.rstrip("/") + h.path, body=body or None, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        if prefix:
            with open(f"{prefix}-response.http", "wb") as f:
                f.write(f"HTTP/1.1 {resp.status} {resp.reason}\r\n".encode())
                f.write(render_headers(resp.getheaders()) + b"\r\n\r\n")
                f.write(redact_body(data))
        h.send_response(resp.status)
        for name, value in resp.getheaders():
            if name.lower() not in HOP and name.lower() != "content-encoding":
                h.send_header(name, value)
        h.send_header("content-length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)


# --------------------------------------------------------------- helpers --


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--build", action="append", required=True, metavar="NAME=BINARY",
                   help="a Bifrost binary to test, for example dev-6c0f795d=/path/bifrost-http")
    p.add_argument("--output", required=True)
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--model", default="openai/gpt-5.4-mini")
    p.add_argument("--extra-model", action="append", default=[],
                   help="also run the deferred minimal case on this model, twice")
    p.add_argument("--claude", action="append", default=[], metavar="NAME",
                   help="also run the Claude Code cases on this build")
    p.add_argument("--groups", default="minimal,responses,direct",
                   help="comma-separated case groups to run for every build")
    p.add_argument("--env-file", help="KEY=VALUE file that holds OPENAI_API_KEY")
    return p.parse_args()


def load_key(env_file):
    key = os.environ.get("OPENAI_API_KEY")
    if not key and env_file:
        for line in pathlib.Path(env_file).read_text().splitlines():
            if line.startswith("OPENAI_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    if not key:
        sys.exit("OPENAI_API_KEY is not set and --env-file did not provide it")
    return key


def wait_port(port, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.3)
    sys.exit(f"port {port} did not open")


def post(port, path, body, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=600)
    hdrs = {"content-type": "application/json"}
    hdrs.update(headers or {})
    conn.request("POST", path, body=json.dumps(body).encode(), headers=hdrs)
    resp = conn.getresponse()
    return resp.status, resp.read().decode("utf-8", "replace")


def anthropic_post(body):
    return post(CLIENT_RELAY_PORT, "/anthropic/v1/messages", body,
                {"anthropic-version": "2023-06-01", "x-api-key": "sk-kairo-dummy"})


def upstream_statuses(case_dir):
    out = []
    for f in sorted((case_dir / "upstream").glob("*-request.http")):
        if f.read_bytes().startswith(b"POST "):
            resp = f.with_name(f.name.replace("-request", "-response"))
            out.append(int(resp.read_bytes().split(b" ", 2)[1]))
    return out


def visible_text(text):
    """The answer text of a buffered body, or of an SSE body reassembled from its deltas."""
    if not text.lstrip().startswith(("event:", "data:")):
        return text
    parts = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:])
        except ValueError:
            continue
        delta = event.get("delta")
        if isinstance(delta, dict) and delta.get("type") == "text_delta":
            parts.append(delta.get("text", ""))
        elif event.get("type") == "response.output_text.delta":
            parts.append(event.get("delta", ""))
        elif event.get("type") == "error":
            parts.append(json.dumps(event))
    return "".join(parts)


def summarize(case_dir, status, text, **extra):
    text = visible_text(text) + text
    row = {
        "case": str(case_dir),
        "client_status": status,
        "upstream_post_statuses": upstream_statuses(case_dir) if (case_dir / "upstream").exists() else [],
        "namespace_rejection": REJECTION in text,
        "canary_answer": CANARY in text,
    }
    row.update(extra)
    return row


# ----------------------------------------------------------------- cases --


def deferred_body(model, stream):
    body = {
        "model": model,
        "max_tokens": 400,
        "tools": [SEARCH, dict(LOOKUP, defer_loading=True)],
        "messages": [
            {"role": "user", "content": QUESTION},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_kairo_search_1", "name": "ToolSearch",
                                               "input": {"query": f"select:{TOOL}", "max_results": 5}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_kairo_search_1",
                                          "content": [{"type": "tool_reference", "tool_name": TOOL}]}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_kairo_lookup_1", "name": TOOL,
                                               "input": {"order_id": "A-1001"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_kairo_lookup_1",
                                          "content": f"Order A-1001 status: {CANARY}"}]},
        ],
    }
    if stream:
        body["stream"] = True
    return body


def eager_body(model, stream):
    body = deferred_body(model, stream)
    body["tools"] = [SEARCH, LOOKUP]
    body["messages"] = [body["messages"][0]] + body["messages"][3:]
    return body


def run_minimal(relays, root, model, runs, rows):
    for label, make, stream in [
        ("deferred-nonstream", deferred_body, False),
        ("deferred-stream", deferred_body, True),
        ("eager-nonstream", eager_body, False),
        ("eager-stream", eager_body, True),
    ]:
        for run in range(1, runs + 1):
            case = root / "minimal" / f"{label}-{run:02d}"
            relays.into(case)
            status, text = anthropic_post(make(model, stream))
            rows.append(summarize(case, status, text, model=model))
    run_hosted(relays, root, model, runs, rows)


def run_extra_models(relays, root, models, rows):
    for model in models:
        for run in range(1, 3):
            case = root / "minimal" / f"deferred-nonstream-{model.split('/')[-1]}-{run:02d}"
            relays.into(case)
            status, text = anthropic_post(deferred_body(model, False))
            rows.append(summarize(case, status, text, model=model))


def run_hosted(relays, root, model, runs, rows):
    """Turn 1 is generated live; turn 2 replays the assistant content verbatim."""
    tools = [{"type": "tool_search_tool_bm25_20251119", "name": "tool_search_tool_bm25"},
             dict(LOOKUP, defer_loading=True)]
    question = "What is the shipping status of order A-1001? Find the right tool and call it."
    for run in range(1, runs + 1):
        case = root / "minimal" / f"hosted-nonstream-{run:02d}"
        relays.into(case)
        messages = [{"role": "user", "content": question}]
        status1, text1 = anthropic_post({"model": model, "max_tokens": 1024, "tools": tools, "messages": messages})
        turn1 = json.loads(text1) if status1 == 200 else {}
        uses = [b for b in turn1.get("content", []) if b.get("type") == "tool_use" and b.get("name") == TOOL]
        if not uses:
            rows.append(summarize(case, status1, text1, model=model, turn1_called_tool=False))
            continue
        messages += [
            {"role": "assistant", "content": turn1["content"]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": uses[0]["id"],
                                          "content": f"Order A-1001 status: {CANARY}"}]},
        ]
        status2, text2 = anthropic_post({"model": model, "max_tokens": 1024, "tools": tools, "messages": messages})
        rows.append(summarize(case, status2, text2, model=model, turn1_called_tool=True,
                              turn1_blocks=[b.get("type") for b in turn1["content"]]))


def responses_body(model, namespace):
    call = {"type": "function_call", "call_id": "call_kairo_lookup_1", "name": TOOL,
            "arguments": json.dumps({"order_id": "A-1001"})}
    if namespace:
        call["namespace"] = TOOL
    return {
        "model": model,
        "tools": [
            {"type": "tool_search", "execution": "client", "description": SEARCH["description"],
             "parameters": SEARCH["input_schema"]},
            {"type": "function", "name": TOOL, "description": LOOKUP["description"],
             "parameters": LOOKUP["input_schema"], "defer_loading": True},
        ],
        "input": [
            {"role": "user", "content": QUESTION},
            {"type": "tool_search_call", "call_id": "call_kairo_search_1", "execution": "client",
             "status": "completed", "arguments": {"query": f"select:{TOOL}", "max_results": 5}},
            {"type": "tool_search_output", "call_id": "call_kairo_search_1", "execution": "client",
             "status": "completed",
             "tools": [{"type": "function", "name": TOOL, "description": LOOKUP["description"],
                        "parameters": LOOKUP["input_schema"], "defer_loading": True}]},
            call,
            {"type": "function_call_output", "call_id": "call_kairo_lookup_1",
             "output": f"Order A-1001 status: {CANARY}"},
        ],
    }


def run_responses(relays, root, model, runs, rows):
    for label, namespace in [("with-namespace", True), ("without-namespace", False)]:
        for run in range(1, runs + 1):
            case = root / "responses" / f"{label}-{run:02d}"
            relays.into(case)
            status, text = post(CLIENT_RELAY_PORT, "/v1/responses", responses_body(model, namespace),
                                {"authorization": "Bearer sk-kairo-dummy"})
            rows.append(summarize(case, status, text, model=model))


def run_direct(key, root, runs, rows):
    """Send the body Bifrost forwarded for deferred-nonstream-01 straight to OpenAI."""
    src = root / "minimal" / "deferred-nonstream-01" / "upstream"
    forwarded = [f for f in sorted(src.glob("*-request.http")) if f.read_bytes().startswith(b"POST /v1/responses")]
    if not forwarded:
        sys.exit(f"no forwarded request in {src}")
    body = json.loads(forwarded[0].read_bytes().split(b"\r\n\r\n", 1)[1])
    restored = json.loads(json.dumps(body))
    for item in restored["input"]:
        if item.get("type") == "function_call" and item.get("name") == TOOL:
            item["namespace"] = TOOL
    for label, payload in [("as-forwarded", body), ("namespace-restored", restored)]:
        for run in range(1, runs + 1):
            case = root / "direct" / f"{label}-{run:02d}"
            case.mkdir(parents=True, exist_ok=True)
            data = json.dumps(payload).encode()
            conn = http.client.HTTPSConnection("api.openai.com", 443, timeout=600)
            headers = {"content-type": "application/json", "authorization": f"Bearer {key}"}
            conn.request("POST", "/v1/responses", body=data, headers=headers)
            resp = conn.getresponse()
            out = resp.read()
            (case / "001-request.http").write_bytes(
                b"POST /v1/responses HTTP/1.1\r\n"
                + render_headers([("content-type", "application/json"), ("authorization", "x")])
                + b"\r\n\r\n" + data)
            (case / "001-response.http").write_bytes(
                f"HTTP/1.1 {resp.status} {resp.reason}\r\n".encode()
                + render_headers(resp.getheaders()) + b"\r\n\r\n" + redact_body(out))
            text = out.decode("utf-8", "replace")
            rows.append({"case": str(case), "client_status": resp.status,
                         "namespace_rejection": REJECTION in text, "canary_answer": CANARY in text})


def run_claude(relays, root, model, runs, rows):
    if not shutil.which("claude"):
        sys.exit("claude is not on PATH")
    for label, enable in [("tool-search-on", "true"), ("tool-search-off", "false")]:
        for run in range(1, runs + 1):
            case = root / "claude" / f"{label}-{run:02d}"
            relays.into(case)
            home = pathlib.Path(tempfile.mkdtemp(prefix="kairo103-"))
            (home / "work").mkdir()
            ledger = case / "ledger.jsonl"
            mcp = {"mcpServers": {"orders": {"type": "stdio", "command": sys.executable,
                                             "args": [str(RIG / "mcp_server.py")],
                                             "env": {"LEDGER": str(ledger)}}}}
            (home / "mcp.json").write_text(json.dumps(mcp))
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("ANTHROPIC_", "OPENAI_", "CLAUDE_"))}
            env.update({
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{CLIENT_RELAY_PORT}/anthropic",
                "ANTHROPIC_API_KEY": "sk-kairo-dummy",
                "ANTHROPIC_MODEL": model,
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
                "ENABLE_TOOL_SEARCH": enable,
                "CLAUDE_CONFIG_DIR": str(home / "config"),
                "DISABLE_TELEMETRY": "1",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "DISABLE_AUTOUPDATER": "1",
            })
            base = ["claude", "-p", "--mcp-config", str(home / "mcp.json"), "--strict-mcp-config",
                    "--allowedTools", TOOL, "--output-format", "stream-json", "--verbose"]
            first = subprocess.run(base + [CLAUDE_PROMPT], cwd=home / "work", env=env,
                                   stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
            (case / "claude-stdout.jsonl").write_text(first.stdout)
            result = claude_result(first.stdout)
            row = {"case": str(case), "enable_tool_search": enable, "exit_code": first.returncode,
                   "tool_executions": count_lines(ledger), "result": result,
                   "namespace_rejection": REJECTION in result, "canary_answer": CANARY in result,
                   "upstream_post_statuses": upstream_statuses(case)}
            if enable == "true":
                relays.into(case / "continue")
                again = subprocess.run(base + ["--continue", "Please try again."], cwd=home / "work", env=env,
                                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
                (case / "continue" / "claude-stdout.jsonl").write_text(again.stdout)
                retry = claude_result(again.stdout)
                row.update({"continue_exit_code": again.returncode, "continue_result": retry,
                            "continue_namespace_rejection": REJECTION in retry,
                            "continue_upstream_post_statuses": upstream_statuses(case / "continue"),
                            "tool_executions_after_continue": count_lines(ledger)})
            rows.append(row)
            shutil.rmtree(home, ignore_errors=True)


def claude_result(stdout):
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "result":
            return str(event.get("result"))
    return ""


def count_lines(path):
    return sum(1 for line in path.read_text().splitlines() if line.strip()) if path.exists() else 0


# ------------------------------------------------------------------ main --


class Relays:
    def __init__(self):
        self.client = Relay(CLIENT_RELAY_PORT, f"http://127.0.0.1:{GATEWAY_PORT}")
        self.upstream = Relay(UPSTREAM_RELAY_PORT, OPENAI)

    def into(self, case):
        self.client.record_into(case / "client")
        self.upstream.record_into(case / "upstream")

    def idle(self):
        self.client.record_into(None)
        self.upstream.record_into(None)


def main():
    args = parse_args()
    key = load_key(args.env_file)
    out = pathlib.Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    relays = Relays()
    groups = set(args.groups.split(","))
    rows = []
    for spec in args.build:
        name, binary = spec.split("=", 1)
        app = pathlib.Path(tempfile.mkdtemp(prefix="kairo103-bifrost-"))
        shutil.copy(RIG / "config.json", app / "config.json")
        env = dict(os.environ, OPENAI_API_KEY=key)
        relays.idle()
        log = open(out / f"{name}-bifrost.log", "w")
        proc = subprocess.Popen([binary, "-app-dir", str(app), "-port", str(GATEWAY_PORT), "-log-level", "warn"],
                                env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            wait_port(GATEWAY_PORT)
            time.sleep(8)  # plugins finish loading after the port opens
            root = out / name
            if "minimal" in groups:
                run_minimal(relays, root, args.model, args.runs, rows)
                run_extra_models(relays, root, args.extra_model, rows)
            if "responses" in groups:
                run_responses(relays, root, args.model, args.runs, rows)
            if "direct" in groups:
                relays.idle()
                run_direct(key, root, args.runs, rows)
            if name in args.claude:
                run_claude(relays, root, args.model, args.runs, rows)
            relays.idle()
        finally:
            proc.terminate()
            proc.wait(timeout=30)
            log.close()
            shutil.rmtree(app, ignore_errors=True)
    for row in rows:
        row["case"] = str(pathlib.Path(row["case"]).relative_to(out))
    (out / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
    leaked = [str(p) for p in out.rglob("*") if p.is_file() and key.encode() in p.read_bytes()]
    if leaked:
        sys.exit(f"credential found in {leaked}; delete the output")
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
