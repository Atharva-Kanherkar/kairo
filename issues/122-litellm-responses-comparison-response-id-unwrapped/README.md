# 122, LiteLLM Responses API Rejects Encrypted Proxy ID in comparison_response_id and Skips Security Check

- **Upstream**: [BerriAI/litellm#49042](https://github.com/BerriAI/litellm/issues/49042) and [BerriAI/litellm#48816](https://github.com/BerriAI/litellm/pull/48816), open as of 2026-11-01
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main`)
- **Reproduced**: 2026-11-01, macOS, Python 3.12, route `/v1/responses`

## Bug or not

- **Is the expected behavior really the spec?** Yes. OpenAI's Responses API specification documents `prompt_cache_options.comparison_response_id` as accepting a valid response ID (maximum string length 64) for prompt cache diagnostics. LiteLLM's `ResponsesIDSecurity` explicitly states its contract: "Security hook to prevent user B from seeing response from user A."
- **Have maintainers already ruled on it?** Yes. BerriAI maintainers opened issue #49042 and PR #48816 confirming that `comparison_response_id` must be decrypted before reaching OpenAI and checked for user ownership on the proxy.
- **Is the trigger supported usage?** Yes. Using OpenAI Responses API with prompt cache options is standard supported usage.
- **Is a real boundary crossed?** Yes. Tenant isolation boundary: User B can reference User A's private response ID in `comparison_response_id` without triggering the 403 Forbidden check. Upstream wire constraint boundary: OpenAI rejects forwarded requests because LiteLLM sends a 225-character encrypted string instead of unwrapping the 55-character provider ID.
- **What fix would a maintainer ship?** Authorize and decrypt `prompt_cache_options.comparison_response_id` alongside `previous_response_id` in `ResponsesIDSecurity` and `ResponsesAPIRequestUtils` before forwarding requests to OpenAI.
- **Classification**: `bug`

## What breaks

When LiteLLM Proxy operates in multi-tenant mode with `ResponsesIDSecurity`, response IDs returned to clients on `/v1/responses` are wrapped in an encrypted envelope (`resp_<Fernet-encrypted-payload>`) embedding the user ID and team ID. This ensures clients only hold opaque proxy IDs and cannot address responses belonging to other tenants.

When clients make follow-up requests:
1. `previous_response_id` is intercepted by `ResponsesIDSecurity.async_pre_call_hook()`. It validates user ownership (raising HTTP 403 if another user tries to access it) and decrypts the ID back to the underlying 55-character provider response ID before forwarding to OpenAI.
2. In contrast, `prompt_cache_options.comparison_response_id` is completely ignored by `ResponsesIDSecurity` and `ResponsesAPIRequestUtils`:
   - **Cross-Tenant Authorization Gap**: If User B submits User A's encrypted response ID in `prompt_cache_options.comparison_response_id`, no ownership check is performed, bypassing the proxy security hook.
   - **Upstream Rejection**: If User A sends their own legitimate response ID in `prompt_cache_options.comparison_response_id`, the proxy forwards the bloated 225-character encrypted string untouched. OpenAI's API rejects the request with `HTTP 400 Bad Request: Invalid 'prompt_cache_options.comparison_response_id': string too long. Expected a string with maximum length 64, but got a string with length 225 instead.`
   - Consequently, prompt cache diagnostics cannot be utilized through LiteLLM Proxy.

## Wire evidence

- `control-cross-user-previous-id-request.json`: User B request attempting to address User A's response in `previous_response_id`.
- `control-cross-user-previous-id-response.json`: LiteLLM correctly blocks with HTTP 403 Forbidden (`The response id is not associated with the user, who this key belongs to`).
- `bug-cross-user-comparison-id-bypass-request.json`: User B request submitting User A's response in `comparison_response_id`.
- `bug-cross-user-comparison-id-bypass-response.json`: LiteLLM allows the request through (HTTP 200 / pre-call check skipped).
- `control-legitimate-previous-id-request.json`: User A request with `previous_response_id` decrypted to 55 characters.
- `control-legitimate-previous-id-response.json`: Upstream execution succeeds with HTTP 200 OK.
- `bug-forwarded-comparison-id-bloated-request.json`: User A request forwarded with bloated 225-character ID.
- `bug-forwarded-comparison-id-bloated-response.json`: Upstream rejects with HTTP 400 Bad Request (`string_above_max_length`).

## Root cause (if found)

In `litellm/proxy/hooks/responses_id_security.py`:
1. Lines 37-45:
   ```python
   _ADDRESSED_RESPONSE_ID_FIELD_BY_CALL_TYPE: Final[Mapping[str, str]] = MappingProxyType(
       {
           "aresponses": "previous_response_id",
           "acompact_responses": "previous_response_id",
           ...
       }
   )
   ```
   Only maps `"previous_response_id"`, omitting `prompt_cache_options.comparison_response_id`.
2. Lines 134-148:
   `async_pre_call_hook()` only inspects `data.get(addressed_id_field)` at the top level of `data`. It never navigates into `data.get("prompt_cache_options")`.
3. In `litellm/responses/utils.py`:
   Lines 373-383 only decode `previous_response_id` and do not inspect `prompt_cache_options["comparison_response_id"]`.

## Test

- Invariant checker: `responses_comparison_response_id_authorized_and_unwrapped` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_responses_cross_user_comparison_id_bypass_is_violation`
  * `litellm_responses_bloated_comparison_id_forwarded_is_violation`
  * `litellm_responses_cross_user_previous_id_blocked_is_conformant`
  * `litellm_responses_legitimate_previous_id_unwrapped_is_conformant`
  * `litellm_responses_comparison_id_results_matrix_5_runs_reproduce_consistently`
