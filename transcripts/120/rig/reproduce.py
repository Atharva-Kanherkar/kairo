#!/usr/bin/env python3
"""Reproduction rig for Finding 120:
LiteLLM Anthropic streaming guardrails parse transport chunks as complete SSE frames,
silently dropping text and bypassing content filters on fragmented chunks (Upstream #49449).

In `litellm/llms/anthropic/chat/guardrail_translation/handler.py`:
    AnthropicMessagesHandler.get_streaming_string_so_far(responses_so_far):
        text_so_far = ""
        for response in responses_so_far:
            if isinstance(response, bytes):
                text_so_far += self._extract_text_from_sse(response)

`_extract_text_from_sse` decodes each raw transport chunk in isolation and calls
`sse_string.split("\\n\\n")`. When a transport read boundary (from TCP/TLS/HTTP chunking)
falls inside an SSE event (such as `content_block_delta` or `signature_delta`),
the incomplete `data:` line fails `json.loads` with `JSONDecodeError`.
LiteLLM logs a warning:
    "WARNING: Failed to parse JSON from SSE data: ..."
and skips the event. The subsequent chunk contains the trailing fragment without an
`event:` or `data:` header and is also skipped.

As a consequence, the text tokens inside fragmented frames are completely omitted from
`text_so_far`. When a post_call guardrail (such as `ContentFilterGuardrail`, PII detector,
or custom moderation hook) inspects `text_so_far`, it scans an incomplete string.
Prohibited keywords, secrets, or policy violations spanning chunk boundaries are never
seen by the guardrail, resulting in a silent safety bypass where the prohibited content
is streamed directly to the client.
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
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

sys.path.insert(0, "/Users/atharva/litellm-main")

ANTHROPIC_PORT = 19998
PROXY_PORT = 19999
MASTER_KEY = "sk-kairo-master-key-12345"
BLOCKED_TOKEN = "BLOCKED_SECRET_TOKEN_49449"

os.environ["LITELLM_MASTER_KEY"] = MASTER_KEY
os.environ["LITELLM_DANGEROUSLY_PERMIT_WEAK_OR_UNSET_MASTER_KEY"] = "true"

WIRE_DIR = Path(__file__).resolve().parent.parent / "wire"
WIRE_DIR.mkdir(parents=True, exist_ok=True)

upstream_app = FastAPI()


def ev(p: dict[str, Any]) -> bytes:
    return ("event: %s\ndata: %s\n\n" % (p["type"], json.dumps(p))).encode("utf-8")


def build_raw_sse_stream() -> bytes:
    parts = [
        ev(
            {
                "type": "message_start",
                "message": {
                    "id": "msg_01",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-3-5-sonnet",
                    "content": [],
                },
            }
        ),
        ev(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            }
        ),
        ev(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "text_delta",
                    "text": f"Confidential payload: {BLOCKED_TOKEN} inside stream.",
                },
            }
        ),
        ev({"type": "content_block_stop", "index": 0}),
        ev({"type": "message_delta", "delta": {"stop_reason": "end_turn"}}),
        ev({"type": "message_stop"}),
    ]
    return b"".join(parts)


@upstream_app.post("/v1/messages")
async def mock_anthropic(request: Request):
    body = await request.json()
    msgs_str = json.dumps(body.get("messages", []))
    is_fragmented = "mode_fragmented" in msgs_str
    whole_bytes = build_raw_sse_stream()

    async def sse_gen():
        if is_fragmented:
            # Split right in the middle of BLOCKED_TOKEN in the text_delta frame
            idx = whole_bytes.find(BLOCKED_TOKEN.encode("utf-8")) + 6
            yield whole_bytes[:idx]
            await asyncio.sleep(0.02)
            yield whole_bytes[idx:]
        else:
            # Whole frames
            yield whole_bytes

    return StreamingResponse(sse_gen(), media_type="text/event-stream")


async def run_reproduction():
    # 1. Start mock Anthropic upstream
    config_upstream = uvicorn.Config(
        upstream_app, host="127.0.0.1", port=ANTHROPIC_PORT, log_level="error"
    )
    server_upstream = uvicorn.Server(config_upstream)
    task_upstream = asyncio.create_task(server_upstream.serve())

    # 2. Configure LiteLLM with content filter guardrail
    cfg = {
        "model_list": [
            {
                "model_name": "claude-3-5-sonnet",
                "litellm_params": {
                    "model": "anthropic/claude-3-5-sonnet",
                    "api_base": f"http://127.0.0.1:{ANTHROPIC_PORT}",
                    "api_key": "fake-key",
                },
            }
        ],
        "general_settings": {
            "master_key": MASTER_KEY,
            "dangerously_permit_weak_or_unset_master_key": True,
        },
        "guardrails": [
            {
                "guardrail_name": "test_content_filter",
                "litellm_params": {
                    "guardrail": "litellm_content_filter",
                    "mode": "post_call",
                    "default_on": True,
                    "patterns": [
                        {
                            "pattern_type": "regex",
                            "name": "blocked_secret",
                            "pattern": BLOCKED_TOKEN,
                            "action": "BLOCK",
                        }
                    ],
                },
            }
        ],
    }

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump(cfg, f)
        cfg_path = f.name

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

    while not server_proxy.started or not server_upstream.started:
        await asyncio.sleep(0.1)

    print(f"Proxy started on http://127.0.0.1:{PROXY_PORT}")
    print(f"Mock Anthropic started on http://127.0.0.1:{ANTHROPIC_PORT}")

    results_list = []
    num_runs = 5

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for run_idx in range(1, num_runs + 1):
                # 1. Control case: whole SSE stream frames
                req_ctrl = {
                    "model": "claude-3-5-sonnet",
                    "messages": [{"role": "user", "content": f"run {run_idx} mode_whole"}],
                    "stream": True,
                    "max_tokens": 100,
                }
                resp_ctrl = await client.post(
                    f"http://127.0.0.1:{PROXY_PORT}/v1/messages",
                    headers={"Authorization": f"Bearer {MASTER_KEY}"},
                    json=req_ctrl,
                )
                body_ctrl = await resp_ctrl.aread()
                body_ctrl_str = body_ctrl.decode("utf-8", "replace")
                ctrl_blocked = resp_ctrl.status_code == 400

                # 2. Bug case: fragmented SSE stream
                req_bug = {
                    "model": "claude-3-5-sonnet",
                    "messages": [{"role": "user", "content": f"run {run_idx} mode_fragmented"}],
                    "stream": True,
                    "max_tokens": 100,
                }
                resp_bug = await client.post(
                    f"http://127.0.0.1:{PROXY_PORT}/v1/messages",
                    headers={"Authorization": f"Bearer {MASTER_KEY}"},
                    json=req_bug,
                )
                body_bug = await resp_bug.aread()
                body_bug_str = body_bug.decode("utf-8", "replace")
                bug_bypassed = resp_bug.status_code == 200 and BLOCKED_TOKEN in body_bug_str

                run_result = {
                    "run": run_idx,
                    "control_proxy_status": resp_ctrl.status_code,
                    "control_blocked_guardrail": ctrl_blocked,
                    "bug_proxy_status": resp_bug.status_code,
                    "bug_bypassed_guardrail": bug_bypassed,
                }
                results_list.append(run_result)
                print(
                    f"Run {run_idx}: Control status={resp_ctrl.status_code} (blocked={ctrl_blocked}) | "
                    f"Bug status={resp_bug.status_code} (bypassed={bug_bypassed})"
                )

                if run_idx == 1:
                    # Save wire evidence
                    (WIRE_DIR / "client-request-control.json").write_text(
                        json.dumps(req_ctrl, indent=2)
                    )
                    try:
                        ctrl_json = json.loads(body_ctrl_str)
                        (WIRE_DIR / "proxy-response-control.json").write_text(
                            json.dumps(ctrl_json, indent=2)
                        )
                    except Exception:
                        (WIRE_DIR / "proxy-response-control.json").write_text(body_ctrl_str)
                    (WIRE_DIR / "client-request-bug.json").write_text(
                        json.dumps(req_bug, indent=2)
                    )
                    (WIRE_DIR / "proxy-response-bug.txt").write_text(body_bug_str)
                    (WIRE_DIR / "upstream-sse-stream.txt").write_text(
                        build_raw_sse_stream().decode("utf-8", "replace")
                    )

        summary_file = WIRE_DIR / "results.json"
        matrix_data = {
            "target_commit": "cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a",
            "target_version": "1.108.0-dev / v1.106.6",
            "upstream_issue": "https://github.com/BerriAI/litellm/issues/49449",
            "runs_completed": num_runs,
            "results": results_list,
        }
        summary_file.write_text(json.dumps(matrix_data, indent=2))
        print(f"Results matrix written to {summary_file}")

    finally:
        server_proxy.should_exit = True
        server_upstream.should_exit = True
        await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(run_reproduction())
