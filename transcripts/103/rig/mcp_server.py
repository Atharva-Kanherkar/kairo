#!/usr/bin/env python3
"""Minimal stdio MCP server: two order tools that log every call to a ledger."""
import json, sys, os
LEDGER = os.environ.get("LEDGER", "/tmp/kairo-ts-ledger.jsonl")
TOOLS = [
    {"name": "lookup_order", "description": "Look up the shipping status of a customer order by its order id.",
     "inputSchema": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}},
    {"name": "refund_order", "description": "Issue a refund for a customer order by its order id.",
     "inputSchema": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}},
]
def send(m):
    sys.stdout.write(json.dumps(m) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    mid, method = msg.get("id"), msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": msg["params"].get("protocolVersion", "2025-06-18"),
              "capabilities": {"tools": {}}, "serverInfo": {"name": "orders", "version": "1.0.0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        name = msg["params"]["name"]; args = msg["params"].get("arguments", {})
        with open(LEDGER, "a") as f:
            f.write(json.dumps({"tool": name, "args": args}) + "\n")
        text = f"Order {args.get('order_id')} status: SHIPPED-CANARY-7731" if name == "lookup_order" else f"Refund issued for {args.get('order_id')}"
        send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}]}})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
