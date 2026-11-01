# Transcript 132: LiteLLM Exact-Match Response Cache Leaks Data Across Tenants

## Overview

This transcript records wire evidence reproducing the defect where LiteLLM's exact-match response cache shares cached completions across distinct tenant identities (virtual keys, teams, and organizations), allowing one tenant to retrieve confidential responses generated for another tenant (Upstream Issue [BerriAI/litellm#49313](https://github.com/BerriAI/litellm/issues/49313), PR [BerriAI/litellm#49576](https://github.com/BerriAI/litellm/pull/49576)).

In `litellm/caching/caching.py`, `Cache.get_cache_key` constructs exact-match cache keys based solely on request parameters (model, messages, temperature, tools, etc.):
```python
def get_cache_key(self, **kwargs: object) -> str:
    preset_cache_key: Final = self.get_preset_cache_key_from_kwargs(**kwargs)
    if preset_cache_key is not None:
        return preset_cache_key
    ...
    hashed_cache_key = self._add_namespace_to_cache_key(hashed_cache_key, **kwargs)
    return hashed_cache_key
```

While semantic caches in LiteLLM scope cache keys by tenant fields (`user_api_key_team_id`, `user_api_key`, `user_api_key_org_id`), exact-match caching completely omits tenant scoping:
- When Tenant A (`user_api_key: "key-a"`, `user_api_key_team_id: "team-a"`) sends a request, the completion is stored under key `K`.
- When Tenant B (`user_api_key: "key-b"`, `user_api_key_team_id: "team-b"`) sends the identical prompt, or sends `cache_key: K` or `cache: {"namespace": ...}`, `get_cache_key` generates the exact same key `K`.
- Tenant B receives Tenant A's cached completion directly from the cache. The downstream provider is never invoked, and confidential data (PII, financial figures, proprietary prompts) is leaked across tenant boundaries.

The fix in PR #49576 introduces `scope_cache_key`, which tags every exact-match cache key with `tenant-<digest>:` computed from `(user_api_key_org_id, user_api_key_team_id, user_api_key)`. When no proxy metadata is present (plain SDK usage), the key remains untagged, preserving standard SDK caching compatibility.

## Reproduction Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- Target Upstream PR: [BerriAI/litellm#49576](https://github.com/BerriAI/litellm/pull/49576)
- Target Upstream Issue: [BerriAI/litellm#49313](https://github.com/BerriAI/litellm/issues/49313)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-01

## Differential Control Ladder

1. **Failing Case (Unpatched LiteLLM Cache with distinct tenants)**:
   - Tenant A caches confidential payroll completion.
   - Tenant B queries cache with identical prompt and distinct tenant credentials.
   - Result: Cache keys match, and Tenant B receives Tenant A's cached response (leak!).
2. **Control 1 (Fixed PR #49576 Cache)**:
   - Scopes cache keys per tenant with `tenant-<digest>:`.
   - Result: Cache keys differ, and Tenant B receives `null` (isolated!).
3. **Control 2 (Forged custom cache_key)**:
   - Tenant B supplies the exact same client-specified `cache_key`.
   - Result: Fixed cache scopes custom keys per tenant identity, isolating Tenant B (evaluates to `null`).
4. **Control 3 (Bare SDK request without tenant metadata)**:
   - Query without tenant metadata.
   - Result: Fixed cache produces identical bare keys to unpatched cache, preserving backwards compatibility.

## Artifacts in `wire/`

- `bug-cross-tenant-cache-leak.json`: Captured unpatched cache output showing identical keys across tenants and Tenant B receiving Tenant A's completion.
- `control-fixed-tenant-cache-isolation.json`: Captured fixed cache output showing distinct scoped keys and `null` cache hit for Tenant B.
- `control-forged-custom-cache-key-isolated.json`: Captured output showing isolation even when identical custom cache keys are passed.
- `control-bare-sdk-key-preserved.json`: Captured output showing bare SDK keys without tenant metadata match unpatched behavior.
- `results.json`: 5 of 5 runs producing identical results across all 4 conditions.
