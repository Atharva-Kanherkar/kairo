# Transcript 124: LiteLLM Response Cache Ignores Destination Connection Params (api_base, api_key)

## Overview

This transcript records wire evidence reproducing the defect in LiteLLM's response cache key generation (Upstream issue [#49551](https://github.com/BerriAI/litellm/issues/49551), open PR [#49563](https://github.com/BerriAI/litellm/pull/49563)).

In `litellm/caching/caching.py`, `Cache.get_cache_key()` calculates cache keys by filtering kwargs through `ModelParamHelper.get_all_llm_api_params()` and `_ALWAYS_KEYED_PROVIDER_PARAMS`. Because connection routing parameters like `api_base` and tenant credentials like `api_key` are classified as LiteLLM-owned kwargs (`is_litellm_owned_kwarg(param) == True`) but are not in `combined_kwargs`, they are completely dropped from the cache key material.

Consequently:
1. Two requests differing only in `api_base` (e.g. `https://cluster-alpha.internal.net/v1` vs `https://cluster-beta.internal.net/v1`) or tenant `api_key` produce identical cache keys.
2. A response from Backend A cached during request A is served as a cache hit to subsequent requests routed to Backend B, causing cross-backend and cross-tenant cache pollution and sensitive data disclosure.

## Reproduction Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- Target Upstream Issue: [BerriAI/litellm#49551](https://github.com/BerriAI/litellm/issues/49551)
- Upstream PR: [BerriAI/litellm#49563](https://github.com/BerriAI/litellm/pull/49563)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-01

## Differential Control Ladder

1. **Failing Case 1 (Cache key collision)**:
   - Generate cache keys for two requests with identical model and prompt, but distinct `api_base` destinations (`https://cluster-alpha.internal.net/v1` vs `https://cluster-beta.internal.net/v1`).
   - Result: Both destinations return identical hash `a77d5dcdb8985f7c1ac2f4b463aa7dc9d58c79de93f2f7ce1bd2fc3ce28ffb26`. Two distinct tenant `api_key` parameters also produce identical hashes.
2. **Failing Case 2 (Cross-backend cache pollution)**:
   - Call `litellm.completion()` with `caching=True` for Backend A with mock response `Cluster Alpha Status: OPERATIONAL...`.
   - Call `litellm.completion()` with `caching=True` for Backend B with mock response `Cluster Beta Status: DEGRADED...`.
   - Result: Backend B call reports `cache_hit: true` and returns Backend A's cached content verbatim, leaking confidential cluster A metrics to cluster B.
3. **Control 1 (Same-endpoint cache hit)**:
   - Repeating the request targeting Backend A correctly returns the cached Backend A content with `cache_hit: true`.
4. **Control 2 (Key separation with keyed connection parameters)**:
   - Including destination parameters (`api_base`, `api_key`) in key material (as introduced by PR #49563) generates separated, non-colliding SHA-256 hashes for Backend A and Backend B.
5. **Control 3 (Direct un-cached isolation)**:
   - Executing both requests with caching disabled (`caching=False`) ensures Backend A receives its response and Backend B receives its distinct response without pollution.

## Artifacts in `wire/`

- `bug-cache-key-collision.json`: Wire capture demonstrating identical cache keys across distinct `api_base` endpoints and tenant `api_key` tokens.
- `bug-cross-backend-cache-pollution.json`: Wire capture demonstrating Backend B receiving Backend A's cached response with `cache_hit: true`.
- `control-same-endpoint-cache-hit.json`: Control verification showing repeated calls to the same endpoint hit cache correctly.
- `control-cache-key-separation.json`: Control verification showing distinct cache keys when connection parameters are keyed.
- `control-un-cached-isolation.json`: Control verification showing un-cached requests remain isolated.
- `results.json`: 5 of 5 runs producing identical results across all 5 conditions.
