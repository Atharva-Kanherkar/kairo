#!/usr/bin/env python3
"""Minimal streamable HTTP MCP server for kairo issue 082."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ["PORT"])
CAPTURE_DIR = Path(os.environ["CAPTURE_DIR"])
LOCK = threading.Lock()
COUNTER = 0
EXECUTION_COUNTER = 0
SESSION_ID = "kairo-082-session"


def compact(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def effect_marker(arguments):
    return "KAIRO_082_EFFECT_%s_%s_%s" % (
        str(arguments["operation"]).upper(),
        arguments["cell"],
        arguments["run"],
    )


TOOLS = [
    {
        "name": "charge",
        "description": "Apply one synthetic side effect.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cell": {"type": "string"},
                "operation": {"type": "string"},
                "phase": {"type": "string"},
                "run": {"type": "string"},
                "source_tool_call_id": {"type": "string"},
            },
            "required": [
                "cell",
                "operation",
                "phase",
                "run",
                "source_tool_call_id",
            ],
        },
    },
    {
        "name": "credit",
        "description": "Apply a distinct synthetic side effect.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cell": {"type": "string"},
                "operation": {"type": "string"},
                "phase": {"type": "string"},
                "run": {"type": "string"},
                "source_tool_call_id": {"type": "string"},
            },
            "required": [
                "cell",
                "operation",
                "phase",
                "run",
                "source_tool_call_id",
            ],
        },
    },
    {
        "name": "review",
        "description": "Manual approval boundary. Never auto-executed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cell": {"type": "string"},
                "operation": {"type": "string"},
                "phase": {"type": "string"},
                "run": {"type": "string"},
                "source_tool_call_id": {"type": "string"},
            },
            "required": [
                "cell",
                "operation",
                "phase",
                "run",
                "source_tool_call_id",
            ],
        },
    },
]


def raw_request(method, target, headers, body):
    lines = ["%s %s HTTP/1.1" % (method, target)]
    lines.extend("%s: %s" % item for item in headers.items())
    return "\r\n".join(lines) + "\r\n\r\n" + body


def raw_response(status, headers, body):
    reason = {200: "OK", 202: "Accepted", 404: "Not Found"}.get(
        status, "Unknown"
    )
    lines = ["HTTP/1.1 %d %s" % (status, reason)]
    lines.extend("%s: %s" % item for item in headers)
    return "\r\n".join(lines) + "\r\n\r\n" + body


def freeze_exchange(method, target, request_headers, request_body, status, response_headers, response_body):
    global COUNTER
    with LOCK:
        COUNTER += 1
        sequence = COUNTER
        prefix = CAPTURE_DIR / ("mcp-%03d" % sequence)
        prefix.with_name(prefix.name + "-request.http").write_text(
            raw_request(method, target, request_headers, request_body)
        )
        prefix.with_name(prefix.name + "-response.http").write_text(
            raw_response(status, response_headers, response_body)
        )
        with (CAPTURE_DIR / "mcp.jsonl").open("a") as capture:
            capture.write(
                compact(
                    {
                        "request_file": prefix.name + "-request.http",
                        "response_file": prefix.name + "-response.http",
                        "sequence": sequence,
                    }
                )
                + "\n"
            )


def record_execution(tool_name, arguments, marker):
    global EXECUTION_COUNTER
    with LOCK:
        EXECUTION_COUNTER += 1
        record = {
            "arguments": arguments,
            "effect_marker": marker,
            "sequence": EXECUTION_COUNTER,
            "tool": tool_name,
            "tool_call_id": arguments["source_tool_call_id"],
        }
        with (CAPTURE_DIR / "executions.jsonl").open("a") as capture:
            capture.write(compact(record) + "\n")


def rpc_result(message):
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        protocol_version = message.get("params", {}).get(
            "protocolVersion", "2025-03-26"
        )
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "kairo-082", "version": "1.0.0"},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": TOOLS},
        }
    if method == "tools/call":
        params = message.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        if tool_name not in {"charge", "credit", "review"}:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": "unknown tool"},
            }
        marker = effect_marker(arguments)
        record_execution(tool_name, arguments, marker)
        output = compact(
            {
                "cell": arguments["cell"],
                "effect_marker": marker,
                "operation": arguments["operation"],
                "phase": arguments["phase"],
                "run": arguments["run"],
                "tool_call_id": arguments["source_tool_call_id"],
            }
        )
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": output}],
                "isError": False,
            },
        }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": "method not found"},
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def respond(self, status, value=None):
        body = "" if value is None else compact(value)
        headers = [("Content-Length", str(len(body.encode())))]
        if body:
            headers.append(("Content-Type", "application/json"))
        headers.append(("Mcp-Session-Id", SESSION_ID))
        freeze_exchange(
            self.command,
            self.path,
            self.headers,
            self.request_body,
            status,
            headers,
            body,
        )
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body.encode())

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        self.request_body = self.rfile.read(length).decode("utf-8", "replace")
        if self.path != "/mcp":
            self.respond(404, {"error": "not found"})
            return
        payload = json.loads(self.request_body or "{}")
        if isinstance(payload, list):
            results = [result for result in map(rpc_result, payload) if result]
            self.respond(200 if results else 202, results or None)
            return
        result = rpc_result(payload)
        self.respond(200 if result else 202, result)

    def do_DELETE(self):
        self.request_body = ""
        self.respond(200, {})


if __name__ == "__main__":
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
