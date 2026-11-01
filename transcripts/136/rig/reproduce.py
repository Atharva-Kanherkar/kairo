#!/usr/bin/env python3
"""Reproduction rig for Finding 136: LiteLLM ROUTE_ENDPOINT_MAPPING omits

anthropic_messages, causing daily spend rows to write endpoint=None and
disrupting Endpoint Activity tracking.

Target: LiteLLM v1.109.0 (commit cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a / main 60fc121ce8a)
Upstream PR: https://github.com/BerriAI/litellm/pull/49581
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import litellm.proxy.db.db_spend_update_writer as writer_module
from litellm.proxy.db.db_spend_update_writer import DBSpendUpdateWriter


async def run_defect_anthropic_messages() -> Dict[str, Any]:
    writer = DBSpendUpdateWriter()
    mock_prisma = MagicMock()
    mock_prisma.get_request_status = MagicMock(return_value="success")

    payload = {
        "request_id": "req-anthropic-messages",
        "user": "user-corp-1",
        "call_type": "anthropic_messages",
        "startTime": "2026-11-02T10:00:00",
        "api_key": "sk-user-key",
        "model": "claude-sonnet-4-5",
        "custom_llm_provider": "anthropic",
        "model_group": "claude-sonnet-4-5",
        "prompt_tokens": 150,
        "completion_tokens": 80,
        "spend": 0.0025,
        "metadata": "{\"usage_object\": {}}",
    }

    writer.daily_spend_update_queue.add_update = AsyncMock()
    await writer.add_spend_log_transaction_to_daily_user_transaction(payload, mock_prisma)

    call_args = writer.daily_spend_update_queue.add_update.call_args[1]
    update_dict = call_args["update"]
    key, transaction = next(iter(update_dict.items()))

    return {
        "call_type": "anthropic_messages",
        "transaction_key": key,
        "endpoint": transaction.get("endpoint"),
        "has_endpoint": transaction.get("endpoint") is not None,
        "ends_with_endpoint": key.endswith("_/messages"),
    }


async def run_control_acompletion() -> Dict[str, Any]:
    writer = DBSpendUpdateWriter()
    mock_prisma = MagicMock()
    mock_prisma.get_request_status = MagicMock(return_value="success")

    payload = {
        "request_id": "req-chat",
        "user": "user-corp-1",
        "call_type": "acompletion",
        "startTime": "2026-11-02T10:00:00",
        "api_key": "sk-user-key",
        "model": "gpt-4o",
        "custom_llm_provider": "openai",
        "model_group": "gpt-4o",
        "prompt_tokens": 150,
        "completion_tokens": 80,
        "spend": 0.0025,
        "metadata": "{\"usage_object\": {}}",
    }

    writer.daily_spend_update_queue.add_update = AsyncMock()
    await writer.add_spend_log_transaction_to_daily_user_transaction(payload, mock_prisma)

    call_args = writer.daily_spend_update_queue.add_update.call_args[1]
    update_dict = call_args["update"]
    key, transaction = next(iter(update_dict.items()))

    return {
        "call_type": "acompletion",
        "transaction_key": key,
        "endpoint": transaction.get("endpoint"),
        "has_endpoint": transaction.get("endpoint") is not None,
        "ends_with_endpoint": key.endswith("_/chat/completions"),
    }


async def run_control_fixed_anthropic_messages() -> Dict[str, Any]:
    writer = DBSpendUpdateWriter()
    mock_prisma = MagicMock()
    mock_prisma.get_request_status = MagicMock(return_value="success")

    orig_mapping = writer_module.ROUTE_ENDPOINT_MAPPING
    patched = dict(orig_mapping)
    patched["anthropic_messages"] = "/messages"
    writer_module.ROUTE_ENDPOINT_MAPPING = patched

    try:
        payload = {
            "request_id": "req-anthropic-fixed",
            "user": "user-corp-1",
            "call_type": "anthropic_messages",
            "startTime": "2026-11-02T10:00:00",
            "api_key": "sk-user-key",
            "model": "claude-sonnet-4-5",
            "custom_llm_provider": "anthropic",
            "model_group": "claude-sonnet-4-5",
            "prompt_tokens": 150,
            "completion_tokens": 80,
            "spend": 0.0025,
            "metadata": "{\"usage_object\": {}}",
        }

        writer.daily_spend_update_queue.add_update = AsyncMock()
        await writer.add_spend_log_transaction_to_daily_user_transaction(payload, mock_prisma)

        call_args = writer.daily_spend_update_queue.add_update.call_args[1]
        update_dict = call_args["update"]
        key, transaction = next(iter(update_dict.items()))

        return {
            "call_type": "anthropic_messages",
            "transaction_key": key,
            "endpoint": transaction.get("endpoint"),
            "has_endpoint": transaction.get("endpoint") is not None,
            "ends_with_endpoint": key.endswith("_/messages"),
        }
    finally:
        writer_module.ROUTE_ENDPOINT_MAPPING = orig_mapping


async def run_control_aresponses() -> Dict[str, Any]:
    writer = DBSpendUpdateWriter()
    mock_prisma = MagicMock()
    mock_prisma.get_request_status = MagicMock(return_value="success")

    payload = {
        "request_id": "req-responses",
        "user": "user-corp-1",
        "call_type": "aresponses",
        "startTime": "2026-11-02T10:00:00",
        "api_key": "sk-user-key",
        "model": "gpt-5.6-sol",
        "custom_llm_provider": "openai",
        "model_group": "gpt-5.6-sol",
        "prompt_tokens": 150,
        "completion_tokens": 80,
        "spend": 0.0025,
        "metadata": "{\"usage_object\": {}}",
    }

    writer.daily_spend_update_queue.add_update = AsyncMock()
    await writer.add_spend_log_transaction_to_daily_user_transaction(payload, mock_prisma)

    call_args = writer.daily_spend_update_queue.add_update.call_args[1]
    update_dict = call_args["update"]
    key, transaction = next(iter(update_dict.items()))

    return {
        "call_type": "aresponses",
        "transaction_key": key,
        "endpoint": transaction.get("endpoint"),
        "has_endpoint": transaction.get("endpoint") is not None,
        "ends_with_endpoint": key.endswith("_/responses"),
    }


async def main():
    wire_dir = Path(__file__).resolve().parents[1] / "wire"
    wire_dir.mkdir(parents=True, exist_ok=True)

    iterations = 5
    run_records = []

    for i in range(iterations):
        defect = await run_defect_anthropic_messages()
        ctrl_chat = await run_control_acompletion()
        ctrl_fixed = await run_control_fixed_anthropic_messages()
        ctrl_responses = await run_control_aresponses()

        assert defect["has_endpoint"] is False
        assert defect["endpoint"] is None
        assert defect["ends_with_endpoint"] is False

        assert ctrl_chat["has_endpoint"] is True
        assert ctrl_chat["endpoint"] == "/chat/completions"
        assert ctrl_chat["ends_with_endpoint"] is True

        assert ctrl_fixed["has_endpoint"] is True
        assert ctrl_fixed["endpoint"] == "/messages"
        assert ctrl_fixed["ends_with_endpoint"] is True

        assert ctrl_responses["has_endpoint"] is True
        assert ctrl_responses["endpoint"] == "/responses"
        assert ctrl_responses["ends_with_endpoint"] is True

        run_records.append({
            "run": i + 1,
            "defect_anthropic_messages_missing_endpoint": True,
            "control_acompletion_has_endpoint": True,
            "control_fixed_anthropic_has_endpoint": True,
            "control_aresponses_has_endpoint": True,
        })

    defect_sample = await run_defect_anthropic_messages()
    ctrl_chat_sample = await run_control_acompletion()
    ctrl_fixed_sample = await run_control_fixed_anthropic_messages()
    ctrl_responses_sample = await run_control_aresponses()

    (wire_dir / "bug-anthropic-messages-spend-row-missing-endpoint.json").write_text(
        json.dumps(defect_sample, indent=2) + "\n"
    )
    (wire_dir / "control-acompletion-spend-row-chat-endpoint.json").write_text(
        json.dumps(ctrl_chat_sample, indent=2) + "\n"
    )
    (wire_dir / "control-fixed-anthropic-messages-spend-row-messages-endpoint.json").write_text(
        json.dumps(ctrl_fixed_sample, indent=2) + "\n"
    )
    (wire_dir / "control-aresponses-spend-row-responses-endpoint.json").write_text(
        json.dumps(ctrl_responses_sample, indent=2) + "\n"
    )

    results = {
        "iterations": iterations,
        "success_rate": f"{iterations}/{iterations}",
        "all_passed": True,
        "runs": run_records,
    }
    (wire_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    (Path(__file__).resolve().parents[1] / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"Reproduced 136: {iterations}/{iterations} runs passed successfully.")


if __name__ == "__main__":
    asyncio.run(main())
