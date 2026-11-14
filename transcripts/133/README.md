# Transcript 133: LiteLLM Responses API Bridge Fails to Flatten Nested user_location in Web Search Tool

## Overview

This transcript records wire evidence reproducing the defect where LiteLLM's Responses API bridge forwards nested `user_location` objects (`tools[...].user_location.approximate`) from Chat Completions `web_search_options` directly into outbound `/v1/responses` requests instead of flattening the fields into the top level of `user_location` (Upstream PR [BerriAI/litellm#49517](https://github.com/BerriAI/litellm/pull/49517)).

In `litellm/completion_extras/litellm_responses_transformation/transformation.py`, `LiteLLMResponsesTransformationHandler._add_web_search_tool` transforms `web_search_options`:
```python
def _add_web_search_tool(
    self,
    responses_api_request: ResponsesAPIOptionalRequestParams,
    web_search_options: object,
) -> None:
    ...
    web_search_tool: Final[dict[str, object]] = {"type": "web_search"}
    if isinstance(web_search_options, dict):
        web_search_tool.update(web_search_options)

    tools.append(cast(Any, web_search_tool))
```

In OpenAI Chat Completions, callers pass approximate geo-location nested under `approximate`:
```json
{
  "web_search_options": {
    "search_context_size": "medium",
    "user_location": {
      "type": "approximate",
      "approximate": {
        "country": "US",
        "city": "San Francisco"
      }
    }
  }
}
```

However, OpenAI's Responses API (`/v1/responses`) expects `user_location` to be flat:
```json
{
  "type": "web_search",
  "search_context_size": "medium",
  "user_location": {
    "type": "approximate",
    "country": "US",
    "city": "San Francisco"
  }
}
```

When LiteLLM forwards the unflattened nested `approximate` object:
- OpenAI `/v1/responses` rejects the request with HTTP 400 Bad Request (`Unknown parameter: 'tools[0].user_location.approximate'`).
- The entire chat completion fails, crashing agent workflows attempting to use geo-targeted web search.

The fix in PR #49517 introduces `_responses_web_search_user_location`, which extracts `country` and `city` from `approximate` and flattens them into `user_location`.

## Reproduction Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- Target Upstream PR: [BerriAI/litellm#49517](https://github.com/BerriAI/litellm/pull/49517)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-01

## Differential Control Ladder

1. **Failing Case (Unpatched LiteLLM Responses Bridge)**:
   - Call with `web_search_options` containing nested `approximate` location.
   - Result: Outbound `tools[0].user_location` contains nested `approximate` object, causing downstream OpenAI 400 rejection.
2. **Control 1 (Fixed PR #49517 Responses Bridge)**:
   - Transforms `user_location` by flattening `approximate` fields (`country`, `city`) into the top level of `user_location`.
   - Result: Outbound `tools[0].user_location` has flat structure without `approximate` key.
3. **Control 2 (Web search without location)**:
   - Transform `web_search_options` without `user_location`.
   - Result: Generates `web_search` tool preserving other parameters (`search_context_size: "low"`) without injecting location.
4. **Control 3 (Plain request without web search)**:
   - Transform request without `web_search_options`.
   - Result: No `web_search` tool is emitted.

## Artifacts in `wire/`

- `bug-responses-nested-user-location.json`: Captured outbound payload showing nested `user_location.approximate`.
- `control-fixed-responses-flat-user-location.json`: Captured outbound payload with flattened `user_location`.
- `control-web-search-no-location.json`: Captured outbound payload for web search without location.
- `control-plain-request-no-web-search.json`: Captured outbound payload for plain request without tools.
- `downstream-openai-rejection-400.json`: Simulated downstream rejection showing HTTP 400 with `Unknown parameter: 'tools[0].user_location.approximate'`.
- `results.json`: 5 of 5 runs producing identical results across all conditions.
