"""Minimal streamable-HTTP MCP server (JSON responses) with side-effect logging."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCHEMA = {"type": "object", "properties": {"invoice": {"type": "string"}, "call": {"type": "string"}},
          "required": ["invoice", "call"]}
TOOLS = [{"name": "charge", "description": "Charge one invoice (side effect).", "inputSchema": SCHEMA},
         {"name": "credit", "description": "Credit one invoice (side effect).", "inputSchema": SCHEMA},
         {"name": "review", "description": "Manual approval boundary.", "inputSchema": SCHEMA}]


def marker(tool, args):
    return f"EFFECT-{tool}-{args.get('invoice')}"


class MCPServer:
    def __init__(self):
        self.executions = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, status, obj=None):
                body = b"" if obj is None else json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                if body:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Mcp-Session-Id", "kairo-session")
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))) or b"{}")
                if isinstance(payload, list):
                    results = [r for r in map(outer._rpc, payload) if r]
                    self._send(200 if results else 202, results or None)
                else:
                    r = outer._rpc(payload)
                    self._send(200 if r else 202, r)

            def do_DELETE(self):
                self._send(200, {})

            def do_GET(self):
                self._send(405, {"error": "no SSE stream"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/mcp"

    def _rpc(self, msg):
        method, rid = msg.get("method"), msg.get("id")
        if rid is None:
            return None
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-03-26"),
                "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "kairo", "version": "1"}}}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": rid, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
        if method == "tools/call":
            name = msg.get("params", {}).get("name")
            args = msg.get("params", {}).get("arguments") or {}
            with self._lock:
                self.executions.append({"tool": name, "arguments": args})
            text = json.dumps({"effect": marker(name, args), "call": args.get("call")})
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}], "isError": False}}
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}}

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
