# Transcript 136: LiteLLM ROUTE_ENDPOINT_MAPPING Omits anthropic_messages, Leaving Daily Spend Rows with Null Endpoint

## Overview

This transcript records wire evidence reproducing the defect where LiteLLM's `ROUTE_ENDPOINT_MAPPING` in `litellm/proxy/route_llm_request.py` lacks a mapping for `call_type = "anthropic_messages"`, causing daily spend log aggregation in `DBSpendUpdateWriter` to record `endpoint = None` and disrupting endpoint-level spend tracking (Upstream PR [BerriAI/litellm#49581](https://github.com/BerriAI/litellm/pull/49581)).

In `litellm/proxy/route_llm_request.py`, `ROUTE_ENDPOINT_MAPPING` maps internal route call types to their canonical public REST endpoint paths:
```python
ROUTE_ENDPOINT_MAPPING: Final = {
    "acompletion": "/chat/completions",
    "atext_completion": "/completions",
    "aresponses": "/responses",
    "arerank": "/rerank",
    "acreate_batch": "/batches",
    ...
}
```

Calls to the Anthropic-native `/v1/messages` endpoint are executed with `call_type = "anthropic_messages"`. However, `"anthropic_messages"` was missing from `ROUTE_ENDPOINT_MAPPING`.

When `DBSpendUpdateWriter.add_spend_log_transaction_to_daily_user_transaction` aggregates spend rows:
```python
call_type: Final = payload.get("call_type", None)
endpoint = None
if call_type:
    endpoint = ROUTE_ENDPOINT_MAPPING.get(call_type, None)
```
Because `"anthropic_messages"` is absent:
1. `endpoint` evaluates to `None`.
2. The daily spend transaction is constructed with `endpoint=None`.
3. The composite dictionary key for queue updates is formatted as `f"{user}_{date}_{api_key}_{model}_{provider}_{endpoint_str}"`, resulting in a trailing underscore (`..._anthropic_`) without endpoint attribution.
4. When persisted to the database (`LiteLLM_DailyUserSpend`, `LiteLLM_DailyTeamSpend`, `LiteLLM_DailyTagSpend`), the `endpoint` column is stored as `NULL`.
5. In the LiteLLM Proxy Dashboard under "Usage > Endpoint Activity", queries grouping by `endpoint` display zero requests and $0 spend for `/messages`, even though model and user spend views count the usage.
6. In `raise_if_required_body_param_missing`, missing parameter errors are formatted with the raw internal string `anthropic_messages: Missing required parameter: 'messages'.` instead of `/messages: Missing required parameter: 'messages'.`.

The fix in PR #49581 adds `"anthropic_messages": "/messages"` to `ROUTE_ENDPOINT_MAPPING`, restoring endpoint attribution for all Anthropic-native traffic.

## Reproduction Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- Target Upstream PR: [BerriAI/litellm#49581](https://github.com/BerriAI/litellm/pull/49581)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-02

## Differential Control Ladder

1. **Failing Case (Unpatched anthropic_messages)**:
   - Call `DBSpendUpdateWriter.add_spend_log_transaction_to_daily_user_transaction` with `call_type = "anthropic_messages"`.
   - Result: Transaction `endpoint` is `None`, and queue key ends with empty endpoint suffix.
2. **Control 1 (Standard Chat Completions acompletion)**:
   - Call with `call_type = "acompletion"`.
   - Result: Transaction `endpoint` is `"/chat/completions"`, and queue key ends with `_/chat/completions`.
3. **Control 2 (Fixed anthropic_messages with PR #49581 patch)**:
   - Call with `call_type = "anthropic_messages"` and patched `ROUTE_ENDPOINT_MAPPING`.
   - Result: Transaction `endpoint` is `"/messages"`, and queue key ends with `_/messages`.
4. **Control 3 (Responses API aresponses)**:
   - Call with `call_type = "aresponses"`.
   - Result: Transaction `endpoint` is `"/responses"`, and queue key ends with `_/responses`.

## Artifacts in `wire/`

- `bug-anthropic-messages-spend-row-missing-endpoint.json`: Captured transaction payload showing `endpoint: null` and untagged transaction key.
- `control-acompletion-spend-row-chat-endpoint.json`: Captured transaction payload showing `endpoint: "/chat/completions"`.
- `control-fixed-anthropic-messages-spend-row-messages-endpoint.json`: Captured transaction payload under PR #49581 fix showing `endpoint: "/messages"`.
- `control-aresponses-spend-row-responses-endpoint.json`: Captured transaction payload showing `endpoint: "/responses"`.
- `results.json`: 5 of 5 runs producing identical results across all conditions.
