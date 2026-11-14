#!/usr/bin/env python3
"""Reproduction rig for Finding 118:
LiteLLM pass_through_endpoints unconditionally strips `id` from JSON-RPC request bodies.

In `litellm/proxy/pass_through_endpoints/pass_through_endpoints.py:643`:
    litellm_keys_in_body = MappingProxyType(
        {k: _parsed_body.pop(k) for k in types_utils.all_litellm_params if k in _parsed_body}
    )

`all_litellm_params` includes "id". Popping "id" from the request body strips the JSON-RPC
request ID before forwarding upstream (`json=_parsed_body` in line 514).
Per JSON-RPC 2.0 Specification Section 4.1, a request without an "id" is a Notification:
"no Response object MUST be returned to the client."
The upstream server therefore treats the request as a notification and returns HTTP 204 No Content,
or returns `id: null`, permanently breaking client-side response correlation.
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

UPSTREAM_PORT = 19992
PROXY_PORT = 19991
MASTER_KEY = "sk-kairo-master-key"
WIRE_DIR = Path(__file__).resolve().parent.parent / "wire"
WIRE_DIR.mkdir(parents=True, exist_ok=True)

received_upstream_requests: list[dict[str, Any]] = []

upstream_app = FastAPI()


@upstream_app.post("/jsonrpc")
async def jsonrpc_handler(request: Request):
    raw_body = await request.body()
    try:
        body = json.loads(raw_body.decode("utf-8"))
    except Exception:
        body = {"raw": raw_body.decode("utf-8", "replace")}

    received_upstream_requests.append(body)

    # Per JSON-RPC 2.0 Section 4.1:
    # "A Notification is a Request object without an 'id' member.
    #  A Request object that is a Notification signifies the Client's lack of interest
    #  in the corresponding Response object, and as such no Response object MUST be returned to the client."
    req_id = body.get("id")
    if req_id is None:
        # Request is a Notification: return 204 No Content
        return Response(status_code=204)

    # Standard JSON-RPC 2.0 Response echoing the request ID
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "status": "ok",
            "tools": [
                {
                    "name": "calculator",
                    "description": "Performs basic arithmetic operations",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"expr": {"type": "string"}},
                        "required": ["expr"],
                    },
                }
            ],
        },
    }


async def run_reproduction():
    database_url = os.environ.get(
        "DATABASE_URL", "postgresql://atharva@localhost:5432/litellm_kairo"
    )
    os.environ["LITELLM_MASTER_KEY"] = MASTER_KEY
    os.environ["DATABASE_URL"] = database_url

    # Start upstream mock server
    config_upstream = uvicorn.Config(
        app=upstream_app, host="127.0.0.1", port=UPSTREAM_PORT, log_level="error"
    )
    server_upstream = uvicorn.Server(config_upstream)
    task_upstream = asyncio.create_task(server_upstream.serve())
    while not server_upstream.started:
        await asyncio.sleep(0.05)
    print(f"Mock JSON-RPC upstream running on http://127.0.0.1:{UPSTREAM_PORT}/jsonrpc")

    # Create LiteLLM configuration with pass_through_endpoints
    cfg = {
        "model_list": [],
        "general_settings": {
            "master_key": MASTER_KEY,
            "database_url": database_url,
            "pass_through_endpoints": [
                {
                    "path": "/mcp-passthrough",
                    "target": f"http://127.0.0.1:{UPSTREAM_PORT}/jsonrpc",
                    "headers": {},
                }
            ],
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
                received_upstream_requests.clear()

                req_payload = {
                    "jsonrpc": "2.0",
                    "id": run_idx * 100,
                    "method": "tools/list",
                    "params": {},
                }

                # 1. Direct upstream call (Control)
                direct_resp = await client.post(
                    f"http://127.0.0.1:{UPSTREAM_PORT}/jsonrpc",
                    json=req_payload,
                )
                direct_received = received_upstream_requests[-1]
                direct_has_id = (
                    "id" in direct_received and direct_received["id"] == run_idx * 100
                )
                direct_status = direct_resp.status_code
                direct_resp_json = direct_resp.json() if direct_status == 200 else {}
                direct_resp_has_matching_id = (
                    direct_resp_json.get("id") == run_idx * 100
                )

                # 2. Call via LiteLLM pass_through_endpoints (Suspected defect)
                proxy_resp = await client.post(
                    f"http://127.0.0.1:{PROXY_PORT}/mcp-passthrough",
                    headers={"Authorization": f"Bearer {MASTER_KEY}"},
                    json=req_payload,
                )
                proxy_forwarded_to_upstream = received_upstream_requests[-1]
                proxy_forwarded_has_id = "id" in proxy_forwarded_to_upstream
                proxy_status = proxy_resp.status_code

                # 3. Control with non-colliding key (e.g. `client_request_id`)
                non_colliding_payload = {
                    "jsonrpc": "2.0",
                    "client_request_id": run_idx * 100,
                    "method": "tools/list",
                    "params": {},
                }
                control_resp = await client.post(
                    f"http://127.0.0.1:{PROXY_PORT}/mcp-passthrough",
                    headers={"Authorization": f"Bearer {MASTER_KEY}"},
                    json=non_colliding_payload,
                )
                control_forwarded = received_upstream_requests[-1]
                control_preserved_non_colliding = (
                    "client_request_id" in control_forwarded
                    and control_forwarded["client_request_id"] == run_idx * 100
                )

                run_result = {
                    "run": run_idx,
                    "client_request_id": run_idx * 100,
                    "direct_upstream_status": direct_status,
                    "direct_upstream_received_has_id": direct_has_id,
                    "direct_upstream_response_matched_id": direct_resp_has_matching_id,
                    "proxy_forwarded_to_upstream_has_id": proxy_forwarded_has_id,
                    "proxy_response_status": proxy_status,
                    "control_non_colliding_key_preserved": control_preserved_non_colliding,
                }
                results_summary.append(run_result)

                # Save wire evidence on run 1
                if run_idx == 1:
                    (WIRE_DIR / "client-proxy-request.json").write_text(
                        json.dumps(req_payload, indent=2)
                    )
                    (WIRE_DIR / "upstream-received-from-proxy.json").write_text(
                        json.dumps(proxy_forwarded_to_upstream, indent=2)
                    )
                    (WIRE_DIR / "client-proxy-response.txt").write_text(
                        f"HTTP/1.1 {proxy_resp.status_code} {proxy_resp.reason_phrase}\n"
                        + "\n".join(f"{k}: {v}" for k, v in proxy_resp.headers.items())
                        + "\n\n"
                        + proxy_resp.text
                    )
                    (WIRE_DIR / "direct-upstream-request.json").write_text(
                        json.dumps(req_payload, indent=2)
                    )
                    (WIRE_DIR / "direct-upstream-response.json").write_text(
                        json.dumps(direct_resp_json, indent=2)
                    )
                    (WIRE_DIR / "control-non-colliding-request.json").write_text(
                        json.dumps(non_colliding_payload, indent=2)
                    )
                    (WIRE_DIR / "control-non-colliding-forwarded.json").write_text(
                        json.dumps(control_forwarded, indent=2)
                    )

    finally:
        server_proxy.should_exit = True
        await task_proxy
        server_upstream.should_exit = True
        await task_upstream
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
            f"Run {r['run']}: Direct received ID={r['direct_upstream_received_has_id']}, "
            f"Proxy forwarded ID={r['proxy_forwarded_to_upstream_has_id']} (dropped!), "
            f"Proxy response status={r['proxy_response_status']} (204 notification), "
            f"Control non-colliding preserved={r['control_non_colliding_key_preserved']}"
        )


if __name__ == "__main__":
    asyncio.run(run_reproduction())
