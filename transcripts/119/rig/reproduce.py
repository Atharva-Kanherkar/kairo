#!/usr/bin/env python3
"""Reproduction rig for Finding 119:
LiteLLM native Anthropic MCP tool loop drops `is_error` flag from tool_result messages.

In `litellm/responses/mcp/litellm_proxy_mcp_handler.py:execute_tool_calls`:
    When an MCP tool call fails or returns `CallToolResult.isError=True`, `execute_tool_calls`
    formats the error as a plain result string and omits `is_error: True` from the result dict.

In `litellm/llms/anthropic/pass_through/messages/mcp_handler.py:_build_tool_result_message`:
    AnthropicMessagesToolResultParam(
        type="tool_result",
        tool_use_id=str(result.get("tool_call_id") or ""),
        content=str(result.get("result") or ""),
    )
    It unconditionally constructs the tool result without `is_error=True`.

As a consequence, on Turn 2 when LiteLLM forwards the executed tool result back to
Anthropic `/v1/messages`, the `tool_result` content block lacks `"is_error": true`.
Anthropic Claude interprets the error message as valid, successful tool return data,
preventing tool retry, correction, and error handling.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, Request, Response

ANTHROPIC_PORT = 19996
MCP_PORT = 19997
PROXY_PORT = 19995
MASTER_KEY = "sk-kairo-master-key"
WIRE_DIR = Path(__file__).resolve().parent.parent / "wire"
WIRE_DIR.mkdir(parents=True, exist_ok=True)

# Captured requests on Anthropic upstream
anthropic_received_requests: list[dict[str, Any]] = []

anthropic_app = FastAPI()


@anthropic_app.post("/v1/messages")
async def anthropic_messages_handler(request: Request):
    raw_body = await request.body()
    try:
        body = json.loads(raw_body.decode("utf-8"))
    except Exception:
        body = {"raw": raw_body.decode("utf-8", "replace")}

    anthropic_received_requests.append(body)
    messages = body.get("messages", [])

    # Check if this is turn 2 (contains a tool_result in the last user message)
    has_tool_result = False
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        has_tool_result = True
                        break

    if not has_tool_result:
        # Turn 1: model emits tool_use to query the database
        return {
            "id": "msg_turn1",
            "type": "message",
            "role": "assistant",
            "model": "claude-3-7-sonnet",
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_db_01",
                    "name": "query_db",
                    "input": {"sql": "SELECT * FROM users;"},
                }
            ],
            "usage": {"input_tokens": 20, "output_tokens": 15},
        }

    # Turn 2: tool result provided
    return {
        "id": "msg_turn2",
        "type": "message",
        "role": "assistant",
        "model": "claude-3-7-sonnet",
        "stop_reason": "end_turn",
        "content": [
            {
                "type": "text",
                "text": "The database query failed due to connection refused.",
            }
        ],
        "usage": {"input_tokens": 45, "output_tokens": 12},
    }


# Mock MCP server implementing JSON-RPC 2.0
mcp_app = FastAPI()


@mcp_app.post("/mcp")
async def mcp_handler(request: Request):
    raw = await request.body()
    try:
        req = json.loads(raw.decode("utf-8"))
    except Exception:
        return Response(status_code=400)

    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-db-server", "version": "1.0.0"},
            },
        }
    elif method == "notifications/initialized":
        return Response(status_code=204)
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "query_db",
                        "description": "Execute a SQL query against the database",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"sql": {"type": "string"}},
                            "required": ["sql"],
                        },
                    }
                ]
            },
        }
    elif method == "tools/call":
        # Simulates an MCP tool failure (isError=True)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": "Database connection error: Connection refused on port 5432",
                    }
                ],
                "isError": True,
            },
        }

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}}


async def run_reproduction():
    database_url = os.environ.get(
        "DATABASE_URL", "postgresql://atharva@localhost:5432/litellm_kairo"
    )
    os.environ["LITELLM_MASTER_KEY"] = MASTER_KEY
    os.environ["DATABASE_URL"] = database_url

    # Start mock Anthropic upstream server
    config_anthropic = uvicorn.Config(
        app=anthropic_app, host="127.0.0.1", port=ANTHROPIC_PORT, log_level="error"
    )
    server_anthropic = uvicorn.Server(config_anthropic)
    task_anthropic = asyncio.create_task(server_anthropic.serve())
    while not server_anthropic.started:
        await asyncio.sleep(0.05)
    print(f"Mock Anthropic upstream running on http://127.0.0.1:{ANTHROPIC_PORT}/v1/messages")

    # Start mock MCP server
    config_mcp = uvicorn.Config(
        app=mcp_app, host="127.0.0.1", port=MCP_PORT, log_level="error"
    )
    server_mcp = uvicorn.Server(config_mcp)
    task_mcp = asyncio.create_task(server_mcp.serve())
    while not server_mcp.started:
        await asyncio.sleep(0.05)
    print(f"Mock MCP server running on http://127.0.0.1:{MCP_PORT}/mcp")

    # Create LiteLLM configuration
    cfg = {
        "model_list": [
            {
                "model_name": "claude-3-7-sonnet",
                "litellm_params": {
                    "model": "anthropic/claude-3-7-sonnet",
                    "api_base": f"http://127.0.0.1:{ANTHROPIC_PORT}",
                    "api_key": "fake-anthropic-key",
                },
            }
        ],
        "general_settings": {
            "master_key": MASTER_KEY,
            "database_url": database_url,
        },
        "mcp_servers": {
            "test_db": {
                "url": f"http://127.0.0.1:{MCP_PORT}/mcp",
                "transport": "http",
            }
        },
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump(cfg, f)
        cfg_path = f.name

    sys.path.insert(0, "/Users/atharva/litellm-main")
    from litellm.proxy.proxy_server import (
        app as proxy_app,
        initialize,
        cleanup_router_config_variables,
    )

    cleanup_router_config_variables()
    await initialize(config=cfg_path, debug=False)

    config_proxy = uvicorn.Config(
        proxy_app, host="127.0.0.1", port=PROXY_PORT, log_level="error"
    )
    server_proxy = uvicorn.Server(config_proxy)
    task_proxy = asyncio.create_task(server_proxy.serve())
    while not server_proxy.started:
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.5)

    print(f"LiteLLM proxy running on http://127.0.0.1:{PROXY_PORT}")

    results_summary = []
    num_runs = 5

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for run_idx in range(1, num_runs + 1):
                anthropic_received_requests.clear()

                client_req_payload = {
                    "model": "claude-3-7-sonnet",
                    "max_tokens": 100,
                    "messages": [{"role": "user", "content": f"Query database run {run_idx}"}],
                    "tools": [
                        {
                            "type": "mcp",
                            "server_url": "litellm_proxy/test_db",
                            "require_approval": "never",
                        }
                    ],
                }

                # 1. Call via LiteLLM proxy
                proxy_resp = await client.post(
                    f"http://127.0.0.1:{PROXY_PORT}/v1/messages",
                    headers={"Authorization": f"Bearer {MASTER_KEY}"},
                    json=client_req_payload,
                )
                proxy_status = proxy_resp.status_code

                # Turn 2 forwarded request received by Anthropic upstream
                turn2_request = anthropic_received_requests[-1] if len(anthropic_received_requests) >= 2 else {}
                turn2_messages = turn2_request.get("messages", [])

                turn2_tool_result = None
                for msg in turn2_messages:
                    if msg.get("role") == "user":
                        for block in msg.get("content", []):
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                turn2_tool_result = block
                                break

                has_tool_result = turn2_tool_result is not None
                is_error_flag_present = (
                    turn2_tool_result is not None and "is_error" in turn2_tool_result
                )
                is_error_value = (
                    turn2_tool_result.get("is_error") if turn2_tool_result else None
                )

                # 2. Control call: Direct to Anthropic with is_error=True
                control_req_payload = {
                    "model": "claude-3-7-sonnet",
                    "max_tokens": 100,
                    "messages": [
                        {"role": "user", "content": "Query database"},
                        {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_control_01",
                                    "name": "query_db",
                                    "input": {"sql": "SELECT 1;"},
                                }
                            ],
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_control_01",
                                    "content": "Database connection error: Connection refused on port 5432",
                                    "is_error": True,
                                }
                            ],
                        },
                    ],
                }
                control_resp = await client.post(
                    f"http://127.0.0.1:{ANTHROPIC_PORT}/v1/messages",
                    json=control_req_payload,
                )
                control_received = anthropic_received_requests[-1]
                control_tool_result = None
                for msg in control_received.get("messages", []):
                    if msg.get("role") == "user":
                        for block in msg.get("content", []):
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                control_tool_result = block
                                break
                control_has_is_error = (
                    control_tool_result is not None
                    and control_tool_result.get("is_error") is True
                )

                run_result = {
                    "run": run_idx,
                    "proxy_status": proxy_status,
                    "turn2_forwarded_has_tool_result": has_tool_result,
                    "turn2_forwarded_is_error_present": is_error_flag_present,
                    "turn2_forwarded_is_error_value": is_error_value,
                    "control_direct_is_error_preserved": control_has_is_error,
                }
                results_summary.append(run_result)

                # Save wire evidence on run 1
                if run_idx == 1:
                    (WIRE_DIR / "client-request.json").write_text(
                        json.dumps(client_req_payload, indent=2)
                    )
                    (WIRE_DIR / "proxy-client-response.json").write_text(
                        json.dumps(proxy_resp.json() if proxy_status == 200 else {}, indent=2)
                    )
                    (WIRE_DIR / "anthropic-turn1-request.json").write_text(
                        json.dumps(anthropic_received_requests[0], indent=2)
                    )
                    (WIRE_DIR / "anthropic-turn2-forwarded-request.json").write_text(
                        json.dumps(turn2_request, indent=2)
                    )
                    (WIRE_DIR / "control-direct-anthropic-request.json").write_text(
                        json.dumps(control_req_payload, indent=2)
                    )
                    (WIRE_DIR / "control-direct-anthropic-response.json").write_text(
                        json.dumps(control_resp.json(), indent=2)
                    )

    finally:
        server_proxy.should_exit = True
        await task_proxy
        server_mcp.should_exit = True
        await task_mcp
        server_anthropic.should_exit = True
        await task_anthropic
        if os.path.exists(cfg_path):
            os.remove(cfg_path)

    # Save results.json
    results_file = WIRE_DIR / "results.json"
    results_file.write_text(
        json.dumps(
            {
                "target_commit": "cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a",
                "target_version": "1.108.0-dev / v1.106.6",
                "runs_completed": num_runs,
                "results": results_summary,
            },
            indent=2,
        )
    )

    print(f"Results recorded in {results_file}:")
    for r in results_summary:
        print(
            f"Run {r['run']}: Proxy status={r['proxy_status']}, "
            f"Turn 2 has tool_result={r['turn2_forwarded_has_tool_result']}, "
            f"Turn 2 is_error present={r['turn2_forwarded_is_error_present']} (defect: False!), "
            f"Control is_error preserved={r['control_direct_is_error_preserved']}"
        )


if __name__ == "__main__":
    asyncio.run(run_reproduction())
