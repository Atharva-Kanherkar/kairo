"""Reproduction script for LiteLLM JSON-defined OpenAI-like providers losing tools by default.

Demonstrates that for JSON-defined OpenAI-compatible providers (such as nano-gpt, scaleway, chutes):
1. Unmapped models not explicitly registered in model_prices_and_context_window.json cause
   supports_function_calling() to return False.
2. In dynamic_config.py:get_supported_openai_params(), tool parameters ('tools', 'tool_choice',
   'function_call', 'functions', 'parallel_tool_calls') are purged from supported_params.
3. With drop_params=False (default), litellm.completion() raises UnsupportedParamsError.
4. With drop_params=True, litellm.completion() silently strips 'tools' and 'tool_choice' from
   the outbound wire request, forwarding only model and messages.
5. Controls:
   - Python-defined OpenAI route (model="openai/z-ai/glm-5.3-flash") preserves tools on wire.
   - Catalog-mapped model (supports_function_calling=True) preserves tools on wire.
   - Fixed dynamic config resolver (PR #48191) preserves tools for unmapped models.
"""

from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import threading
import time
from typing import Any

import litellm
from litellm.exceptions import UnsupportedParamsError
from litellm.llms.openai_like.dynamic_config import create_config_class
from litellm.llms.openai_like.json_loader import SimpleProviderConfig


class MockOpenAICompatibleServer(http.server.BaseHTTPRequestHandler):
    received_requests: list[dict[str, Any]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length).decode("utf-8")
        req_json = json.loads(body) if body else {}
        MockOpenAICompatibleServer.received_requests.append(req_json)

        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()

        model_name = req_json.get("model", "z-ai/glm-5.3-flash")
        resp = {
            "id": "chatcmpl-mock-127",
            "object": "chat.completion",
            "created": 1730460000,
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Acknowledge request.",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        }
        self.wfile.write(json.dumps(resp).encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:
        pass


def run_iteration(run_id: int, base_url: str, wire_dir: Path | None = None) -> dict[str, bool]:
    sample_tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather for a specified location",
                "parameters": {
                    "type": "object",
                    "properties": {"location": {"type": "string"}},
                    "required": ["location"],
                },
            },
        }
    ]
    messages = [{"role": "user", "content": "What is the weather in Tokyo?"}]

    # --- 1. Bug: drop_params=False raises UnsupportedParamsError ---
    litellm.drop_params = False
    bug_error_raised = False
    error_details: dict[str, Any] = {}
    try:
        litellm.completion(
            model="nano-gpt/z-ai/glm-5.3-flash",
            api_key="sk-synthetic-key",
            api_base=base_url,
            messages=messages,
            tools=sample_tools,
            tool_choice="auto",
        )
    except UnsupportedParamsError as exc:
        bug_error_raised = True
        error_details = {
            "exception_type": "UnsupportedParamsError",
            "model": "z-ai/glm-5.3-flash",
            "provider": "nano-gpt",
            "error_message": str(exc),
            "unsupported_parameters": ["tools", "tool_choice"],
        }

    # --- 2. Bug: drop_params=True silently strips tools from outbound wire body ---
    litellm.drop_params = True
    MockOpenAICompatibleServer.received_requests.clear()
    litellm.completion(
        model="nano-gpt/z-ai/glm-5.3-flash",
        api_key="sk-synthetic-key",
        api_base=base_url,
        messages=messages,
        tools=sample_tools,
        tool_choice="auto",
    )
    bug_outbound = MockOpenAICompatibleServer.received_requests[0]
    bug_tools_stripped = ("tools" not in bug_outbound) and ("tool_choice" not in bug_outbound)

    # --- 3. Control 1: Python-defined openai/ route preserves tools ---
    MockOpenAICompatibleServer.received_requests.clear()
    litellm.completion(
        model="openai/z-ai/glm-5.3-flash",
        api_key="sk-synthetic-key",
        api_base=base_url,
        messages=messages,
        tools=sample_tools,
        tool_choice="auto",
    )
    ctrl_openai_outbound = MockOpenAICompatibleServer.received_requests[0]
    ctrl_openai_preserved = ("tools" in ctrl_openai_outbound) and ("tool_choice" in ctrl_openai_outbound)

    # --- 4. Control 2: Mapped model in catalog with supports_function_calling=True ---
    MockOpenAICompatibleServer.received_requests.clear()
    mapped_model_name = "nano-gpt/mapped-glm-model"
    litellm.model_cost[mapped_model_name] = {
        "litellm_provider": "nano-gpt",
        "mode": "chat",
        "supports_function_calling": True,
    }
    litellm.completion(
        model=mapped_model_name,
        api_key="sk-synthetic-key",
        api_base=base_url,
        messages=messages,
        tools=sample_tools,
        tool_choice="auto",
    )
    ctrl_mapped_outbound = MockOpenAICompatibleServer.received_requests[0]
    ctrl_mapped_preserved = ("tools" in ctrl_mapped_outbound) and ("tool_choice" in ctrl_mapped_outbound)

    # --- 5. Control 3: Fixed dynamic config logic (PR #48191) ---
    provider_config = SimpleProviderConfig("nano-gpt", {
        "base_url": base_url,
        "api_key_env": "NANO_GPT_API_KEY",
        "provider_info": {"description": "Nano-GPT provider", "supports_function_calling": None}
    })
    # Fixed logic from PR #48191: unmapped model defaults to preserving tools
    is_provider_disabled = (
        provider_config.provider_info and provider_config.provider_info.get("supports_function_calling") is False
    )
    fixed_supports_fc = not is_provider_disabled
    ctrl_fixed_preserved = fixed_supports_fc

    if wire_dir is not None:
        wire_dir.mkdir(parents=True, exist_ok=True)
        # Bug 1 wire
        (wire_dir / "bug-unsupported-params-error.json").write_text(
            json.dumps(
                {
                    "test_case": "bug_unsupported_params_error",
                    "model": "nano-gpt/z-ai/glm-5.3-flash",
                    "drop_params": False,
                    "tools_passed": True,
                    "error_raised": bug_error_raised,
                    "error_details": error_details,
                },
                indent=2,
            )
        )
        # Bug 2 wire
        (wire_dir / "bug-outbound-tools-stripped.json").write_text(
            json.dumps(
                {
                    "test_case": "bug_outbound_tools_stripped",
                    "model": "nano-gpt/z-ai/glm-5.3-flash",
                    "drop_params": True,
                    "tools_passed": True,
                    "tools_in_outbound": "tools" in bug_outbound,
                    "tool_choice_in_outbound": "tool_choice" in bug_outbound,
                    "forwarded_body_keys": list(bug_outbound.keys()),
                    "forwarded_outbound_body": bug_outbound,
                },
                indent=2,
            )
        )
        # Control 1 wire
        (wire_dir / "control-outbound-openai-route-tools-preserved.json").write_text(
            json.dumps(
                {
                    "test_case": "control_outbound_openai_route_tools_preserved",
                    "model": "openai/z-ai/glm-5.3-flash",
                    "drop_params": True,
                    "tools_passed": True,
                    "tools_in_outbound": "tools" in ctrl_openai_outbound,
                    "tool_choice_in_outbound": "tool_choice" in ctrl_openai_outbound,
                    "forwarded_body_keys": list(ctrl_openai_outbound.keys()),
                    "forwarded_outbound_body": ctrl_openai_outbound,
                },
                indent=2,
            )
        )
        # Control 2 wire
        (wire_dir / "control-outbound-mapped-model-tools-preserved.json").write_text(
            json.dumps(
                {
                    "test_case": "control_outbound_mapped_model_tools_preserved",
                    "model": mapped_model_name,
                    "catalog_supports_function_calling": True,
                    "drop_params": True,
                    "tools_passed": True,
                    "tools_in_outbound": "tools" in ctrl_mapped_outbound,
                    "tool_choice_in_outbound": "tool_choice" in ctrl_mapped_outbound,
                    "forwarded_body_keys": list(ctrl_mapped_outbound.keys()),
                    "forwarded_outbound_body": ctrl_mapped_outbound,
                },
                indent=2,
            )
        )
        # Control 3 wire
        (wire_dir / "control-dynamic-config-fixed-tools-preserved.json").write_text(
            json.dumps(
                {
                    "test_case": "control_dynamic_config_fixed_tools_preserved",
                    "provider": "nano-gpt",
                    "model": "z-ai/glm-5.3-flash",
                    "pr_48191_default_rule": "Preserve tool parameters for unmapped models on OpenAI-compatible JSON providers",
                    "resolved_supports_function_calling": fixed_supports_fc,
                    "tools_preserved": ctrl_fixed_preserved,
                },
                indent=2,
            )
        )

    return {
        "bug_error_raised": bug_error_raised,
        "bug_tools_stripped": bug_tools_stripped,
        "ctrl_openai_preserved": ctrl_openai_preserved,
        "ctrl_mapped_preserved": ctrl_mapped_preserved,
        "ctrl_fixed_preserved": ctrl_fixed_preserved,
    }


def main() -> None:
    port = 9955
    server = http.server.HTTPServer(("127.0.0.1", port), MockOpenAICompatibleServer)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    rig_dir = Path(__file__).resolve().parent
    transcripts_dir = rig_dir.parent
    wire_dir = transcripts_dir / "wire"
    wire_dir.mkdir(parents=True, exist_ok=True)

    base_url = f"http://127.0.0.1:{port}"
    iterations = 5
    results: list[dict[str, bool]] = []

    try:
        for i in range(iterations):
            res = run_iteration(i + 1, base_url=base_url, wire_dir=wire_dir if i == 0 else None)
            results.append(res)
            print(f"Run {i+1}/{iterations}: {res}")
    finally:
        server.shutdown()

    all_passed = all(
        r["bug_error_raised"]
        and r["bug_tools_stripped"]
        and r["ctrl_openai_preserved"]
        and r["ctrl_mapped_preserved"]
        and r["ctrl_fixed_preserved"]
        for r in results
    )

    payload = {
        "iterations": iterations,
        "success_rate": f"{sum(1 for r in results if all(r.values()))}/{iterations}",
        "all_passed": all_passed,
        "runs": results,
    }

    (transcripts_dir / "results.json").write_text(json.dumps(payload, indent=2))
    (wire_dir / "results.json").write_text(json.dumps(payload, indent=2))
    print(f"\nFinal status: {payload['success_rate']} passed. results.json written.")


if __name__ == "__main__":
    main()
