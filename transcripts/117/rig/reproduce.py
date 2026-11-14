import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route
import uvicorn
import yaml

UPSTREAM_PORT = 19996
PROXY_PORT = 19995
TARGET_COMMIT = "cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a"
TARGET_VERSION = "1.108.0-dev / v1.106.6"

WIRE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "wire")
)
os.makedirs(WIRE_DIR, exist_ok=True)

SIGNATURE_VALUE = "SIG-claude-thought-signature-12345"


async def mock_openrouter(request):
    body = await request.body()
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    messages = data.get("messages", [])
    stream = data.get("stream", False)

    # Check for multi-turn replay validation
    for msg in messages:
        if msg.get("role") == "assistant":
            # If assistant message has reasoning content/details but lacks signature
            reasoning = msg.get("reasoning") or msg.get("reasoning_content")
            details = msg.get("reasoning_details") or (
                msg.get("provider_specific_fields", {}).get("reasoning_details")
            )
            has_sig = False
            if details and isinstance(details, list):
                for item in details:
                    if isinstance(item, dict) and item.get("signature"):
                        has_sig = True
            if reasoning and not has_sig:
                return JSONResponse(
                    {
                        "error": {
                            "code": 400,
                            "message": (
                                "Invalid thought signature: thinking blocks in"
                                " assistant message history must include a"
                                " valid cryptographic signature."
                            ),
                            "type": "invalid_request_error",
                        }
                    },
                    status_code=400,
                )

    if stream:
        async def sse_gen():
            # Chunk 1: reasoning text delta
            c1 = {
                "id": "gen-test-117",
                "object": "chat.completion.chunk",
                "created": 1730000000,
                "model": "anthropic/claude-sonnet-4.5",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "reasoning": "Let me solve this step by step.",
                            "reasoning_details": [
                                {
                                    "type": "reasoning.text",
                                    "text": "Let me solve this step by step.",
                                }
                            ],
                        },
                    }
                ],
            }
            yield f"data: {json.dumps(c1)}\n\n"
            await asyncio.sleep(0.01)

            # Chunk 2: signature-only delta (closing reasoning block)
            c2 = {
                "id": "gen-test-117",
                "object": "chat.completion.chunk",
                "created": 1730000000,
                "model": "anthropic/claude-sonnet-4.5",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "reasoning_details": [
                                {
                                    "type": "reasoning.text",
                                    "signature": SIGNATURE_VALUE,
                                }
                            ]
                        },
                    }
                ],
            }
            yield f"data: {json.dumps(c2)}\n\n"
            await asyncio.sleep(0.01)

            # Chunk 3: answer content delta
            c3 = {
                "id": "gen-test-117",
                "object": "chat.completion.chunk",
                "created": 1730000000,
                "model": "anthropic/claude-sonnet-4.5",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "The answer is 42."},
                        "finish_reason": "stop",
                    }
                ],
            }
            yield f"data: {json.dumps(c3)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(sse_gen(), media_type="text/event-stream")

    return JSONResponse({
        "id": "gen-test-117",
        "object": "chat.completion",
        "created": 1730000000,
        "model": "anthropic/claude-sonnet-4.5",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Turn completed successfully.",
                },
                "finish_reason": "stop",
            }
        ],
    })


app_upstream = Starlette(
    routes=[Route("/api/v1/chat/completions", mock_openrouter, methods=["POST"])]
)


def start_upstream():
    config = uvicorn.Config(
        app_upstream, host="127.0.0.1", port=UPSTREAM_PORT, log_level="error"
    )
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    return server


async def run_reproduction():
    up_server = start_upstream()
    os.environ["LITELLM_MASTER_KEY"] = "sk-master-key-117"
    if "DATABASE_URL" not in os.environ:
        os.environ["DATABASE_URL"] = (
            "postgresql://atharva@localhost:5432/litellm_kairo"
        )

    # Proxy config
    cfg = {
        "model_list": [
            {
                "model_name": "claude-sonnet-4.5",
                "litellm_params": {
                    "model": "openrouter/anthropic/claude-sonnet-4.5",
                    "api_base": f"http://127.0.0.1:{UPSTREAM_PORT}/api/v1",
                    "api_key": "fake-openrouter-key",
                },
            }
        ],
        "general_settings": {"master_key": "sk-master-key-117"},
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

    results = []

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for run_idx in range(1, 6):
                stream_req = {
                    "model": "claude-sonnet-4.5",
                    "messages": [{"role": "user", "content": "Calculate 42"}],
                    "stream": True,
                }

                # 1. Direct upstream stream
                resp_up = await client.post(
                    f"http://127.0.0.1:{UPSTREAM_PORT}/api/v1/chat/completions",
                    json={
                        "model": "anthropic/claude-sonnet-4.5",
                        "messages": [
                            {"role": "user", "content": "Calculate 42"}
                        ],
                        "stream": True,
                    },
                )
                up_text = resp_up.text
                up_has_sig = SIGNATURE_VALUE in up_text

                # 2. LiteLLM proxy stream
                resp_proxy = await client.post(
                    f"http://127.0.0.1:{PROXY_PORT}/v1/chat/completions",
                    headers={"Authorization": "Bearer sk-master-key-117"},
                    json=stream_req,
                )
                proxy_text = resp_proxy.text
                proxy_has_sig = SIGNATURE_VALUE in proxy_text

                # Parse proxy chunks
                proxy_chunks = []
                for line in proxy_text.splitlines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        try:
                            proxy_chunks.append(json.loads(line[6:].strip()))
                        except Exception:
                            pass

                # Next turn with signature preserved (control)
                next_turn_with_sig_req = {
                    "model": "claude-sonnet-4.5",
                    "messages": [
                        {"role": "user", "content": "Calculate 42"},
                        {
                            "role": "assistant",
                            "content": "The answer is 42.",
                            "reasoning": "Let me solve this step by step.",
                            "reasoning_details": [
                                {
                                    "type": "reasoning.text",
                                    "text": "Let me solve this step by step.",
                                },
                                {
                                    "type": "reasoning.text",
                                    "signature": SIGNATURE_VALUE,
                                },
                            ],
                        },
                        {"role": "user", "content": "Are you sure?"},
                    ],
                }
                resp_next_with_sig = await client.post(
                    f"http://127.0.0.1:{UPSTREAM_PORT}/api/v1/chat/completions",
                    json=next_turn_with_sig_req,
                )

                # Next turn missing signature (as rebuilt after proxy drop)
                next_turn_missing_sig_req = {
                    "model": "claude-sonnet-4.5",
                    "messages": [
                        {"role": "user", "content": "Calculate 42"},
                        {
                            "role": "assistant",
                            "content": "The answer is 42.",
                            "reasoning": "Let me solve this step by step.",
                            "reasoning_details": [
                                {
                                    "type": "reasoning.text",
                                    "text": "Let me solve this step by step.",
                                }
                            ],
                        },
                        {"role": "user", "content": "Are you sure?"},
                    ],
                }
                resp_next_missing_sig = await client.post(
                    f"http://127.0.0.1:{UPSTREAM_PORT}/api/v1/chat/completions",
                    json=next_turn_missing_sig_req,
                )

                results.append({
                    "run": run_idx,
                    "direct_upstream_status": resp_up.status_code,
                    "direct_upstream_has_signature": up_has_sig,
                    "litellm_proxy_status": resp_proxy.status_code,
                    "litellm_proxy_has_signature": proxy_has_sig,
                    "litellm_proxy_chunks_count": len(proxy_chunks),
                    "next_turn_with_signature_status": (
                        resp_next_with_sig.status_code
                    ),
                    "next_turn_missing_signature_status": (
                        resp_next_missing_sig.status_code
                    ),
                })

                if run_idx == 1:
                    with open(
                        os.path.join(
                            WIRE_DIR, "direct-upstream-stream-request.json"
                        ),
                        "w",
                    ) as f_out:
                        json.dump(
                            {
                                "model": "anthropic/claude-sonnet-4.5",
                                "messages": [
                                    {"role": "user", "content": "Calculate 42"}
                                ],
                                "stream": True,
                            },
                            f_out,
                            indent=2,
                        )
                    with open(
                        os.path.join(
                            WIRE_DIR, "direct-upstream-stream-response.txt"
                        ),
                        "w",
                    ) as f_out:
                        f_out.write(up_text)

                    with open(
                        os.path.join(
                            WIRE_DIR, "litellm-proxy-stream-request.json"
                        ),
                        "w",
                    ) as f_out:
                        json.dump(stream_req, f_out, indent=2)
                    with open(
                        os.path.join(
                            WIRE_DIR, "litellm-proxy-stream-response.txt"
                        ),
                        "w",
                    ) as f_out:
                        f_out.write(proxy_text)

                    with open(
                        os.path.join(
                            WIRE_DIR, "next-turn-with-signature-request.json"
                        ),
                        "w",
                    ) as f_out:
                        json.dump(next_turn_with_sig_req, f_out, indent=2)
                    with open(
                        os.path.join(
                            WIRE_DIR, "next-turn-with-signature-response.json"
                        ),
                        "w",
                    ) as f_out:
                        json.dump(resp_next_with_sig.json(), f_out, indent=2)

                    with open(
                        os.path.join(
                            WIRE_DIR,
                            "next-turn-missing-signature-request.json",
                        ),
                        "w",
                    ) as f_out:
                        json.dump(next_turn_missing_sig_req, f_out, indent=2)
                    with open(
                        os.path.join(
                            WIRE_DIR,
                            "next-turn-missing-signature-response.json",
                        ),
                        "w",
                    ) as f_out:
                        json.dump(
                            resp_next_missing_sig.json(), f_out, indent=2
                        )

        matrix = {
            "target_commit": TARGET_COMMIT,
            "target_version": TARGET_VERSION,
            "runs_completed": len(results),
            "results": results,
        }
        with open(os.path.join(WIRE_DIR, "results.json"), "w") as f_out:
            json.dump(matrix, f_out, indent=2)

        print(f"Reproduction finished: {len(results)}/5 runs.")
        for r in results:
            print(
                f"Run {r['run']}: Direct sig={r['direct_upstream_has_signature']}, "
                f"LiteLLM sig={r['litellm_proxy_has_signature']}, "
                f"Next with sig={r['next_turn_with_signature_status']}, "
                f"Next missing sig={r['next_turn_missing_signature_status']}"
            )

    finally:
        server_proxy.should_exit = True
        up_server.should_exit = True
        await task_proxy
        if os.path.exists(cfg_path):
            os.remove(cfg_path)


if __name__ == "__main__":
    asyncio.run(run_reproduction())
