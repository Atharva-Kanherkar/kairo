"""Deterministic OpenAI chat upstream for MCP agent mode, plus the gateway config.

The first user turn names a scenario: "SCENARIO repeated|distinct|single". The
model answers with auto-executable calls plus one manual `review` call.
"""

import json
import re

from kairo_verify import Reply
from rigs import bifrost

MODEL = "mockoai/mimo-v2.5"
PLANS = {"repeated": [("charge", "alpha"), ("charge", "beta")],
         "distinct": [("charge", "alpha"), ("credit", "beta")],
         "single": [("charge", "alpha")]}


def _call(tool, invoice, n):
    cid = f"call_{tool}_{invoice}_{n}"
    return {"id": cid, "type": "function",
            "function": {"name": f"Billing-{tool}", "arguments": json.dumps({"invoice": invoice, "call": cid})}}


def respond(cap):
    if cap.method == "GET":
        return Reply.json({"object": "list", "data": [{"id": "mimo-v2.5", "object": "model"}]})
    body = cap.json or {}
    m = re.search(r"SCENARIO (\w+)", json.dumps(body.get("messages")))
    if not m or m.group(1) not in PLANS:
        msg, finish = {"role": "assistant", "content": "no scenario"}, "stop"
    else:
        calls = [_call(t, inv, i) for i, (t, inv) in enumerate(PLANS[m.group(1)])] + [_call("review", "all", 9)]
        msg, finish = {"role": "assistant", "content": None, "tool_calls": calls}, "tool_calls"
    return Reply.json({"id": "chatcmpl-kairo", "object": "chat.completion", "created": 0, "model": "mimo-v2.5",
                       "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                       "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}})


def gateway_config(upstream_url, mcp_url):
    return bifrost.config(
        {"mockoai": {"keys": [{"name": "k", "value": "sk-kairo-provider", "weight": 1, "models": ["*"]}],
                     "network_config": {"base_url": upstream_url, "max_retries": 0},
                     "custom_provider_config": {"base_provider_type": "openai"}}},
        config_store={"enabled": True, "type": "sqlite", "config": {"path": "__APP_DIR__/config.db"}},
        logs_store={"enabled": False},
        mcp={"client_configs": [{"auth_type": "none", "client_id": "kairo-billing", "connection_string": mcp_url,
                                 "connection_type": "http", "is_ping_available": False, "name": "Billing",
                                 "needs_session_stickiness": True, "tools_to_auto_execute": ["charge", "credit"],
                                 "tools_to_execute": ["*"]}],
             "tool_manager_config": {"max_agent_depth": 5, "tool_execution_timeout": "5s"}},
    )


def chat_body(scenario):
    return {"model": MODEL, "stream": False, "temperature": 0,
            "messages": [{"role": "user", "content": f"SCENARIO {scenario}: settle the invoices"}]}
