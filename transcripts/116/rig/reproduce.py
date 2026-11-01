import asyncio
import json
import os
import sys
import tempfile
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
import httpx
import yaml

UPSTREAM_PORT = 19998
PROXY_PORT = 19997
TARGET_COMMIT = "cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a"
TARGET_VERSION = "1.108.0-dev / v1.106.6"

WIRE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "wire")
)
os.makedirs(WIRE_DIR, exist_ok=True)


async def upstream_mcp(request):
    if request.method == "GET":
        return Response(status_code=404)

    auth = request.headers.get("authorization", "")
    body = await request.body()
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    method = data.get("method")
    req_id = data.get("id")

    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-upstream", "version": "1.0"},
            },
        })
    elif method == "notifications/initialized":
        return Response(status_code=202)
    elif method == "tools/list":
        if "expired-token" in auth:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": "Token expired"},
                },
                status_code=401,
                headers={
                    "WWW-Authenticate": 'Bearer error="invalid_token", error_description="The access token expired"'
                },
            )
        else:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "calculator",
                            "description": "Performs basic math",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "expr": {"type": "string"}
                                },
                            },
                        }
                    ]
                },
            })
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {}})


app_upstream = Starlette(
    routes=[Route("/mcp", upstream_mcp, methods=["POST", "GET"])]
)


def parse_sse_event_data(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data: "):
            try:
                return json.loads(line[6:].strip())
            except Exception:
                pass
    try:
        return json.loads(text)
    except Exception:
        return {}


async def run():
    # 1. Start Upstream MCP Server
    config_up = uvicorn.Config(
        app_upstream, host="127.0.0.1", port=UPSTREAM_PORT, log_level="error"
    )
    server_up = uvicorn.Server(config_up)
    task_up = asyncio.create_task(server_up.serve())
    await asyncio.sleep(0.5)

    # 2. Write LiteLLM Config
    config_dict = {
        "mcp_servers": {
            "test_server": {
                "url": f"http://127.0.0.1:{UPSTREAM_PORT}/mcp",
                "transport": "http",
                "auth_type": "true_passthrough",
            }
        },
        "general_settings": {
            "master_key": "sk-litellm-master-key",
        },
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump(config_dict, f)
        config_path = f.name

    os.environ["LITELLM_MASTER_KEY"] = "sk-litellm-master-key"
    if "DATABASE_URL" not in os.environ:
        os.environ["DATABASE_URL"] = (
            "postgresql://atharva@localhost:5432/litellm_kairo"
        )

    # 3. Start LiteLLM Proxy
    sys.path.insert(0, "/Users/atharva/litellm-main")
    from litellm.proxy.proxy_server import (
        app as proxy_app,
        initialize,
        cleanup_router_config_variables,
    )
    from litellm.proxy._experimental.mcp_server.mcp_server_manager import (
        global_mcp_server_manager,
    )
    from litellm.proxy._experimental.mcp_server.catalog import CatalogSnapshots

    cleanup_router_config_variables()
    global_mcp_server_manager.catalog = CatalogSnapshots(global_mcp_server_manager)
    await initialize(config=config_path, debug=False)

    config_proxy = uvicorn.Config(
        proxy_app, host="127.0.0.1", port=PROXY_PORT, log_level="error"
    )
    server_proxy = uvicorn.Server(config_proxy)
    task_proxy = asyncio.create_task(server_proxy.serve())
    await asyncio.sleep(1.0)

    results = []

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for run_idx in range(1, 6):
                # Condition 1: Direct upstream with valid token
                direct_valid_req = {
                    "jsonrpc": "2.0",
                    "id": run_idx,
                    "method": "tools/list",
                    "params": {},
                }
                resp_up_valid = await client.post(
                    f"http://127.0.0.1:{UPSTREAM_PORT}/mcp",
                    headers={
                        "Authorization": "Bearer valid-token",
                        "Content-Type": "application/json",
                    },
                    json=direct_valid_req,
                )

                # Condition 2: Direct upstream with expired token
                direct_expired_req = {
                    "jsonrpc": "2.0",
                    "id": run_idx,
                    "method": "tools/list",
                    "params": {},
                }
                resp_up_expired = await client.post(
                    f"http://127.0.0.1:{UPSTREAM_PORT}/mcp",
                    headers={
                        "Authorization": "Bearer expired-token",
                        "Content-Type": "application/json",
                    },
                    json=direct_expired_req,
                )

                # Condition 3: LiteLLM with valid token
                litellm_valid_req = {
                    "jsonrpc": "2.0",
                    "id": run_idx,
                    "method": "tools/list",
                    "params": {},
                }
                resp_litellm_valid = await client.post(
                    f"http://127.0.0.1:{PROXY_PORT}/test_server/mcp",
                    headers={
                        "Authorization": "Bearer valid-token",
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                    },
                    json=litellm_valid_req,
                )

                # Condition 4: LiteLLM with expired token (the trigger)
                litellm_expired_req = {
                    "jsonrpc": "2.0",
                    "id": run_idx,
                    "method": "tools/list",
                    "params": {},
                }
                resp_litellm_expired = await client.post(
                    f"http://127.0.0.1:{PROXY_PORT}/test_server/mcp",
                    headers={
                        "Authorization": "Bearer expired-token",
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                    },
                    json=litellm_expired_req,
                )

                parsed_litellm_valid = parse_sse_event_data(
                    resp_litellm_valid.text
                )
                parsed_litellm_expired = parse_sse_event_data(
                    resp_litellm_expired.text
                )

                results.append({
                    "run": run_idx,
                    "direct_upstream_valid_status": resp_up_valid.status_code,
                    "direct_upstream_expired_status": resp_up_expired.status_code,
                    "direct_upstream_expired_www_authenticate": resp_up_expired.headers.get(
                        "www-authenticate"
                    ),
                    "litellm_valid_status": resp_litellm_valid.status_code,
                    "litellm_valid_tool_count": len(
                        parsed_litellm_valid.get("result", {}).get("tools", [])
                    ),
                    "litellm_expired_status": resp_litellm_expired.status_code,
                    "litellm_expired_www_authenticate": resp_litellm_expired.headers.get(
                        "www-authenticate"
                    ),
                    "litellm_expired_tool_count": len(
                        parsed_litellm_expired.get("result", {}).get("tools", [])
                    ),
                    "litellm_expired_server_outcome": (
                        parsed_litellm_expired.get("result", {})
                        .get("_meta", {})
                        .get("litellm.ai/server_outcomes", {})
                        .get("test_server", {})
                    ),
                })

                # Write wire artifacts from run 1
                if run_idx == 1:
                    with open(
                        os.path.join(
                            WIRE_DIR, "direct-upstream-valid-request.json"
                        ),
                        "w",
                    ) as out_f:
                        json.dump(direct_valid_req, out_f, indent=2)
                    with open(
                        os.path.join(
                            WIRE_DIR, "direct-upstream-valid-response.json"
                        ),
                        "w",
                    ) as out_f:
                        json.dump(resp_up_valid.json(), out_f, indent=2)

                    with open(
                        os.path.join(
                            WIRE_DIR, "direct-upstream-expired-request.json"
                        ),
                        "w",
                    ) as out_f:
                        json.dump(direct_expired_req, out_f, indent=2)
                    with open(
                        os.path.join(
                            WIRE_DIR, "direct-upstream-expired-response.json"
                        ),
                        "w",
                    ) as out_f:
                        json.dump(resp_up_expired.json(), out_f, indent=2)

                    with open(
                        os.path.join(WIRE_DIR, "litellm-valid-request.json"),
                        "w",
                    ) as out_f:
                        json.dump(litellm_valid_req, out_f, indent=2)
                    with open(
                        os.path.join(WIRE_DIR, "litellm-valid-response.json"),
                        "w",
                    ) as out_f:
                        json.dump(parsed_litellm_valid, out_f, indent=2)

                    with open(
                        os.path.join(WIRE_DIR, "litellm-expired-request.json"),
                        "w",
                    ) as out_f:
                        json.dump(litellm_expired_req, out_f, indent=2)
                    with open(
                        os.path.join(
                            WIRE_DIR, "litellm-expired-response.json"
                        ),
                        "w",
                    ) as out_f:
                        json.dump(parsed_litellm_expired, out_f, indent=2)

                    with open(
                        os.path.join(WIRE_DIR, "litellm-expired-raw.txt"), "w"
                    ) as out_f:
                        out_f.write(
                            f"HTTP/1.1 {resp_litellm_expired.status_code} OK\n"
                        )
                        for k, v in resp_litellm_expired.headers.items():
                            out_f.write(f"{k}: {v}\n")
                        out_f.write("\n")
                        out_f.write(resp_litellm_expired.text)

        matrix = {
            "target_commit": TARGET_COMMIT,
            "target_version": TARGET_VERSION,
            "runs_completed": len(results),
            "results": results,
        }
        with open(os.path.join(WIRE_DIR, "results.json"), "w") as out_f:
            json.dump(matrix, out_f, indent=2)

        print(f"Reproduction completed successfully: {len(results)}/5 runs.")
        for r in results:
            print(
                f"Run {r['run']}: "
                f"Direct Expired: {r['direct_upstream_expired_status']}, "
                f"LiteLLM Valid: {r['litellm_valid_status']} (tools={r['litellm_valid_tool_count']}), "
                f"LiteLLM Expired: {r['litellm_expired_status']} (tools={r['litellm_expired_tool_count']}, "
                f"outcome={r['litellm_expired_server_outcome']})"
            )

    finally:
        server_proxy.should_exit = True
        server_up.should_exit = True
        await task_proxy
        await task_up
        if os.path.exists(config_path):
            os.remove(config_path)


if __name__ == "__main__":
    asyncio.run(run())
