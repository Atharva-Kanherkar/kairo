# 133, LiteLLM Responses API Bridge Emits Nested user_location in Web Search Tool, Causing OpenAI HTTP 400 Rejections

- **Upstream**: Open PR [BerriAI/litellm#49517](https://github.com/BerriAI/litellm/pull/49517), open as of 2026-11-01
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- **Reproduced**: 2026-11-01, macOS, Python 3.12, module `litellm.completion_extras.litellm_responses_transformation.transformation`

## Bug or not

- **Is the expected behavior really the spec?** Yes. OpenAI's Responses API (`/v1/responses`) specification defines the `web_search` tool with a flat `user_location` shape: `{"type": "approximate", "country": "...", "city": "..."}`. Forwarding the nested Chat Completions format (`{"type": "approximate", "approximate": {"country": "...", "city": "..."}}`) violates the Responses API schema, prompting OpenAI to reject the call with HTTP 400 Bad Request.
- **Have maintainers already ruled on it?** Yes. In PR #49517 (`fix(openai): keep web_search_options when a chat completions request bridges to the Responses API`), maintainers and contributors recognized that Chat Completions nests location under `approximate`, whereas Responses API rejects that key (`Unknown parameter: 'tools[0].user_location.approximate'`) and requires flattened fields.
- **Is the trigger supported usage?** Yes. Sending a Chat Completions request to a GPT-5 model (e.g. `openai/responses/gpt-5.6-sol` or tool-bridged `gpt-5.6-sol`) with geo-targeted `web_search_options` is standard, documented usage.
- **Is a real boundary crossed?** Yes. Provider wire validation boundary. The gateway transmits an invalid wire parameter schema to OpenAI, causing the downstream provider to reject the request with HTTP 400.
- **What fix would a maintainer ship?** In `litellm/completion_extras/litellm_responses_transformation/transformation.py`, flatten `user_location.approximate` into `user_location` when building the `web_search` tool for the Responses API.
- **Classification**: `bug`

## What breaks

When a caller sends a Chat Completions request with geo-targeted web search to a model bridged to OpenAI's Responses API:
1. The client configures `web_search_options: {"search_context_size": "medium", "user_location": {"type": "approximate", "approximate": {"country": "US", "city": "San Francisco"}}}`.
2. In `litellm/completion_extras/litellm_responses_transformation/transformation.py`, `_add_web_search_tool` copies `web_search_options` directly into `tools` without flattening the nested `approximate` dictionary.
3. The outbound request sent to OpenAI `/v1/responses` contains `"tools": [{"type": "web_search", "user_location": {"type": "approximate", "approximate": {"country": "US", "city": "San Francisco"}}}]`.
4. OpenAI's `/v1/responses` endpoint rejects the request with HTTP 400 Bad Request (`Unknown parameter: 'tools[0].user_location.approximate'`).
5. The entire completion call crashes, failing downstream agent pipelines that rely on geo-located web search results.

## Wire evidence

- `bug-responses-nested-user-location.json`: Captured outbound payload showing nested `user_location.approximate`.
- `control-fixed-responses-flat-user-location.json`: Captured outbound payload under PR #49517 fix showing flattened `user_location`.
- `control-web-search-no-location.json`: Captured outbound payload showing web search tool without location.
- `control-plain-request-no-web-search.json`: Captured outbound payload for plain request without tools.
- `downstream-openai-rejection-400.json`: Captured downstream rejection from OpenAI API showing HTTP 400 `Unknown parameter: 'tools[0].user_location.approximate'`.
- `results.json`: 5 of 5 runs reproducing 100% deterministically across all conditions.

## Root cause (if found)

In `litellm/completion_extras/litellm_responses_transformation/transformation.py`:
```python
web_search_tool: Final[dict[str, object]] = {"type": "web_search"}
if isinstance(web_search_options, dict):
    web_search_tool.update(web_search_options)

tools.append(cast(Any, web_search_tool))
```
`web_search_tool.update(web_search_options)` preserves the Chat Completions nested `approximate` format without adapting it to the Responses API wire contract.

The fix introduces flattening:
```python
@staticmethod
def _responses_web_search_user_location(user_location: object) -> Mapping[str, object] | None:
    if not isinstance(user_location, dict):
        return None
    typed_location = cast(Mapping[object, object], user_location)
    approximate = typed_location.get("approximate")
    if not isinstance(approximate, dict):
        return None
    typed_approximate = cast(Mapping[object, object], approximate)
    return {
        "type": "approximate",
        **{str(key): value for key, value in typed_approximate.items() if value is not None},
    }
```

## Test

- Invariant checker: `responses_api_web_search_user_location_flat` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_responses_nested_user_location_wire_reproduction`
  * `litellm_responses_fixed_flat_user_location_control`
  * `litellm_responses_web_search_no_location_control`
  * `litellm_responses_plain_no_web_search_control`
  * `litellm_responses_user_location_matrix_5_runs_reproduce_consistently`
