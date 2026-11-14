# 124, LiteLLM Response Cache Ignores Destination Connection Params (api_base, api_key)

- **Upstream**: [BerriAI/litellm#49551](https://github.com/BerriAI/litellm/issues/49551), open as of 2026-11-01; PR [BerriAI/litellm#49563](https://github.com/BerriAI/litellm/pull/49563)
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- **Reproduced**: 2026-11-01, macOS, Python 3.12, module `litellm.caching.caching`

## Bug or not

- **Is the expected behavior really the spec?** Yes. An AI gateway cache must partition cached responses by destination and credential boundaries. When two requests target different endpoints or use different API keys, their responses must not collide or be served interchangeably.
- **Have maintainers already ruled on it?** Yes. Maintainers and contributors acknowledged this defect in open PR #49563 (`fix(caching): key the response cache on api_base and api_key`), confirming that `api_base` and `api_key` are routing destinations rather than non-routing connection setup and must be keyed.
- **Is the trigger supported usage?** Yes. Supplying `api_base` (e.g. for self-hosted vLLM/Ollama clusters or Azure deployments) or tenant-specific `api_key` to `litellm.completion(..., caching=True)` is documented and standard LiteLLM usage.
- **Is a real boundary crossed?** Yes. Cross-backend and cross-tenant boundary breach. A confidential response cached from Backend A is served directly to callers targeting Backend B.
- **What fix would a maintainer ship?** Add `api_base` and `api_key` to keyed connection parameters in `Cache.get_cache_key()` so that requests targeting different endpoints or tenants produce distinct cache hashes.
- **Classification**: `bug`

## What breaks

In `litellm/caching/caching.py`, `Cache.get_cache_key()` generates cache keys by iterating over `kwargs`:
1. It includes a parameter only if `param in combined_kwargs or param in _ALWAYS_KEYED_PROVIDER_PARAMS`.
2. `combined_kwargs` originates from `ModelParamHelper.get_all_llm_api_params()`, which tracks standard model parameters (such as `messages`, `temperature`, `max_tokens`), but omits `api_base` and `api_key`.
3. If not in `combined_kwargs`, the loop checks `elif not is_litellm_owned_kwarg(param):`. Because both `api_base` and `api_key` are registered LiteLLM kwargs (`is_litellm_owned_kwarg` returns `True`), this branch is also skipped.
4. Consequently, neither `api_base` nor `api_key` enters the cache key material. Two completion calls with the same model and prompt targeting completely different backends (e.g. `https://cluster-alpha.internal.net/v1` and `https://cluster-beta.internal.net/v1`) generate the identical SHA-256 cache key.
5. In production, this causes cross-backend cache poisoning:
   - Call 1 to Backend A populates the cache with Backend A's response.
   - Call 2 to Backend B hits the cache and returns Backend A's response with `cache_hit: true`.
   - Callers querying Backend B receive stale, incorrect, or private data belonging to Backend A.

## Wire evidence

- `bug-cache-key-collision.json`: Captured identical SHA-256 cache keys (`a77d5dcdb8985f7c1ac2f4b463aa7dc9d58c79de93f2f7ce1bd2fc3ce28ffb26`) for requests differing only in `api_base` or tenant `api_key`.
- `bug-cross-backend-cache-pollution.json`: Captured completion response from Backend B returning Backend A's private status metrics with `cache_hit: true` and `leak_detected: true`.
- `control-same-endpoint-cache-hit.json`: Verification that repeated requests to the same endpoint hit the cache cleanly.
- `control-cache-key-separation.json`: Verification that incorporating `api_base` and `api_key` into the key material generates distinct, non-colliding cache hashes.
- `control-un-cached-isolation.json`: Verification that un-cached calls to Backend A and Backend B remain completely isolated.

## Root cause (if found)

In `litellm/caching/caching.py`, line 403:
```python
if param in combined_kwargs or param in _ALWAYS_KEYED_PROVIDER_PARAMS:
    param_value: str | None = self._get_param_value(param, kwargs)
    if param in _ALWAYS_KEYED_PROVIDER_PARAMS and not param_value:
        continue
    if param_value is not None:
        cache_key += f"{param}: {param_value}"
elif not is_litellm_owned_kwarg(param):
    ...
```
`api_base` and `api_key` are in `all_litellm_params` (owned kwargs), but are not in `combined_kwargs` (`ModelParamHelper.get_all_llm_api_params()`) or `_ALWAYS_KEYED_PROVIDER_PARAMS`. They fall between the two branches and are dropped from key material.

## Test

- Invariant checker: `response_cache_keys_differentiate_destinations` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_cache_key_collision_ignores_destination_connection_params`
  * `litellm_cache_cross_backend_response_pollution_detected`
  * `litellm_cache_key_separation_control_wire`
  * `litellm_cache_uncached_isolation_control_wire`
  * `litellm_cache_same_endpoint_cache_hit_control_wire`
  * `litellm_cache_destination_results_matrix_5_runs_reproduce_consistently`
