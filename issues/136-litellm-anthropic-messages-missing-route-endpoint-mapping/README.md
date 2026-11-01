# 136, LiteLLM ROUTE_ENDPOINT_MAPPING Omits anthropic_messages, Leaving Daily Spend Rows with Null Endpoint

- **Upstream**: Open PR [BerriAI/litellm#49581](https://github.com/BerriAI/litellm/pull/49581), open as of 2026-11-01
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- **Reproduced**: 2026-11-02, macOS, Python 3.12, module `litellm.proxy.route_llm_request` and `litellm.proxy.db.db_spend_update_writer`

## Bug or not

- **Is the expected behavior really the spec?** Yes. LiteLLM proxy's spend accounting contract (`db_spend_update_writer.py`) maps incoming call types to canonical endpoint paths using `ROUTE_ENDPOINT_MAPPING` so that daily spend tables (`DailyUserSpend`, `DailyTeamSpend`, `DailyTagSpend`) record the endpoint for activity reporting. All other endpoints (`/chat/completions`, `/responses`, `/rerank`, `/batches`) have mappings; omitting `anthropic_messages` was an unintended omission.
- **Have maintainers already ruled on it?** Yes. In PR #49581 (`fix(proxy): attribute /v1/messages spend rows to /messages in daily activity`), maintainers confirmed that Anthropic-native requests were logged with call type `anthropic_messages`, which lacked an entry in `ROUTE_ENDPOINT_MAPPING`, causing daily spend rows to be written without an endpoint.
- **Is the trigger supported usage?** Yes. Sending requests to `/v1/messages` using Claude Code, Cursor, or the Anthropic SDK through LiteLLM proxy is standard, supported usage.
- **Is a real boundary crossed?** Yes. Proxy spend accounting and REST error validation boundary. Anthropic spend rows are written to the database with `endpoint: NULL`, blinding endpoint-level usage queries and dashboard activity reports. In addition, parameter validation errors format with the internal string `anthropic_messages` instead of `/messages`.
- **What fix would a maintainer ship?** In `litellm/proxy/route_llm_request.py`, add `"anthropic_messages": "/messages"` to `ROUTE_ENDPOINT_MAPPING` so Anthropic `/v1/messages` requests are attributed to `/messages` in daily spend tracking and parameter validation errors.
- **Classification**: `bug`

## What breaks

When clients send requests to LiteLLM's Anthropic `/v1/messages` endpoint:
1. LiteLLM logs the request with `call_type = "anthropic_messages"`.
2. When `DBSpendUpdateWriter` aggregates spend for user, team, or tag spend tables, it queries `ROUTE_ENDPOINT_MAPPING.get("anthropic_messages", None)`.
3. Because `"anthropic_messages"` is missing from the dictionary, `endpoint` evaluates to `None`.
4. The transaction is queued with `endpoint: None` and a degraded composite key lacking an endpoint suffix (`..._anthropic_`).
5. When written to the database, the `endpoint` column is stored as `NULL`.
6. Operators inspecting the LiteLLM Proxy Dashboard under "Usage > Endpoint Activity" see 0 requests and $0 spend for `/messages`, even though model, user, and key views count the spend. Endpoint-level cost allocation and chargeback reports completely omit Anthropic traffic.

## Wire evidence

- `bug-anthropic-messages-spend-row-missing-endpoint.json`: Captured transaction showing `endpoint: null` and missing key suffix.
- `control-acompletion-spend-row-chat-endpoint.json`: Captured transaction showing `endpoint: "/chat/completions"`.
- `control-fixed-anthropic-messages-spend-row-messages-endpoint.json`: Captured transaction under PR #49581 fix showing `endpoint: "/messages"`.
- `control-aresponses-spend-row-responses-endpoint.json`: Captured transaction showing `endpoint: "/responses"`.
- `results.json`: 5 of 5 runs reproducing 100% deterministically across all conditions.

## Root cause (if found)

In `litellm/proxy/route_llm_request.py`:
```python
ROUTE_ENDPOINT_MAPPING: Final = {
    "acompletion": "/chat/completions",
    "atext_completion": "/completions",
    "aresponses": "/responses",
    ...
}
```
The dictionary omitted `"anthropic_messages": "/messages"`.

In `litellm/proxy/db/db_spend_update_writer.py`:
```python
call_type: Final = payload.get("call_type", None)
endpoint = None
if call_type:
    endpoint = ROUTE_ENDPOINT_MAPPING.get(call_type, None)
```
Because the key was missing, `endpoint` resolved to `None` for every `/v1/messages` request.

The fix adds `"anthropic_messages": "/messages"` to `ROUTE_ENDPOINT_MAPPING`.

## Test

- Invariant checker: `daily_spend_endpoint_attributed` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_anthropic_messages_missing_endpoint_spend_row_reproduction`
  * `litellm_acompletion_chat_endpoint_spend_row_control`
  * `litellm_fixed_anthropic_messages_messages_endpoint_control`
  * `litellm_aresponses_responses_endpoint_spend_row_control`
  * `litellm_anthropic_messages_endpoint_matrix_5_runs_reproduce_consistently`
