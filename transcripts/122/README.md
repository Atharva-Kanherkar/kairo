# Transcript 122: LiteLLM Responses API Rejects Encrypted Proxy ID in comparison_response_id and Skips Security Check

## Overview

This transcript records wire evidence reproducing the defect in LiteLLM Proxy's Responses API response ID handling (Upstream issue [#49042](https://github.com/BerriAI/litellm/issues/49042) and PR [#48816](https://github.com/BerriAI/litellm/pull/48816)).

When `ResponsesIDSecurity` is enabled on LiteLLM Proxy:
1. Turn 1 returns an encrypted response ID (225+ characters, formatted `resp_<Fernet-encrypted-payload>`) embedding ownership metadata (`user_id`, `team_id`) to prevent unauthorized cross-user response ID access.
2. In `litellm/proxy/hooks/responses_id_security.py`:
   - `_ADDRESSED_RESPONSE_ID_FIELD_BY_CALL_TYPE` maps `"aresponses"` exclusively to `"previous_response_id"`.
   - `async_pre_call_hook()` only inspects `data.get("previous_response_id")` and completely ignores `prompt_cache_options.comparison_response_id`.
3. Consequently, two distinct failures occur:
   - **Cross-Tenant Security Authorization Bypass**: When User B submits User A's encrypted response ID in `prompt_cache_options.comparison_response_id`, the proxy bypasses `check_user_access_to_response_id()` and allows the call through without raising HTTP 403 Forbidden.
   - **Decryption Failure & Provider Protocol Rejection**: When User A (the legitimate owner) submits their own encrypted response ID in `prompt_cache_options.comparison_response_id`, LiteLLM fails to decrypt it back to the original 55-character OpenAI response ID. The proxy forwards the 225+ character string to OpenAI `/v1/responses`, which rejects it with HTTP 400 Bad Request (`string_above_max_length`, expected <= 64).

## Reproduction Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main`)
- Target Upstream Issue: [BerriAI/litellm#49042](https://github.com/BerriAI/litellm/issues/49042)
- Target Upstream PR: [BerriAI/litellm#48816](https://github.com/BerriAI/litellm/pull/48816)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-01

## Differential Control Ladder

1. **Control 1 (Security ownership check on `previous_response_id`)**:
   - User B submits User A's encrypted response ID in `previous_response_id`.
   - Result: Blocked with HTTP 403 Forbidden (`The response id is not associated with the user, who this key belongs to`).
2. **Failing Case 1 (Security ownership bypass on `comparison_response_id`)**:
   - User B submits User A's encrypted response ID in `prompt_cache_options.comparison_response_id`.
   - Result: Hook skips the field. Allowed through without HTTP 403 (security authorization bypass).
3. **Control 2 (Authorized decryption on `previous_response_id`)**:
   - User A submits their own encrypted response ID in `previous_response_id`.
   - Result: Decrypted back to the original 55-character OpenAI response ID (`resp_02517226...`), allowing successful upstream execution.
4. **Failing Case 2 (Decryption failure and upstream 400 rejection)**:
   - User A submits their own encrypted response ID in `prompt_cache_options.comparison_response_id`.
   - Result: Hook leaves the field as a 225-character encrypted string. OpenAI rejects requests exceeding 64 characters with HTTP 400 Bad Request (`string_above_max_length`).

## Artifacts in `wire/`

- `control-cross-user-previous-id-request.json`: User B request attempting to access User A's response via `previous_response_id`.
- `control-cross-user-previous-id-response.json`: LiteLLM HTTP 403 Forbidden response enforcing tenant isolation.
- `bug-cross-user-comparison-id-bypass-request.json`: User B request attempting to access User A's response via `comparison_response_id`.
- `bug-cross-user-comparison-id-bypass-response.json`: LiteLLM response permitting the request (security check skipped).
- `control-legitimate-previous-id-request.json`: User A request with `previous_response_id` properly unwrapped to 55 characters.
- `control-legitimate-previous-id-response.json`: Successful upstream execution with unwrapped response ID.
- `bug-forwarded-comparison-id-bloated-request.json`: User A request forwarded with bloated 225-character encrypted ID.
- `bug-forwarded-comparison-id-bloated-response.json`: OpenAI HTTP 400 rejection (`string_above_max_length`).
- `results.json`: 5 of 5 runs producing identical results.
