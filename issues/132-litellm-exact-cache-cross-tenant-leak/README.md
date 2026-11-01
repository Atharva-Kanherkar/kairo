# 132, LiteLLM Exact-Match Response Cache Leaks Data Across Tenants

- **Upstream**: [BerriAI/litellm#49313](https://github.com/BerriAI/litellm/issues/49313), open PR [BerriAI/litellm#49576](https://github.com/BerriAI/litellm/pull/49576), open as of 2026-11-01
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- **Reproduced**: 2026-11-01, macOS, Python 3.12, module `litellm.caching.caching`

## Bug or not

- **Is the expected behavior really the spec?** Yes. Multi-tenant AI gateways must guarantee tenant isolation across virtual keys, teams, and organizations. The LiteLLM proxy authentication layer issues distinct virtual keys (`user_api_key`), team identifiers (`user_api_key_team_id`), and organization identifiers (`user_api_key_org_id`) specifically to partition budgets, rate limits, and access controls. Caching must never serve one tenant's completion to another tenant.
- **Have maintainers already ruled on it?** Yes. In PR #49576 (`fix(caching): scope exact-match cache keys per virtual key, team and org`), maintainers and contributors confirmed that exact-match caches improperly shared completions across tenants, whereas semantic caches had already enforced tenant scoping.
- **Is the trigger supported usage?** Yes. Deploying LiteLLM Proxy with `cache: true` (e.g. Redis or in-memory) and authenticating requests using standard virtual keys or team assignments is the documented, standard configuration.
- **Is a real boundary crossed?** Yes. Cross-tenant authorization and confidentiality boundary. Distinct tenants with different credentials access private, cached completions without authorization.
- **What fix would a maintainer ship?** In `litellm/caching/caching.py`, prefix exact-match cache keys with `tenant-<digest>:` computed from `(user_api_key_org_id, user_api_key_team_id, user_api_key)` while keeping plain SDK calls without tenant metadata untagged.
- **Classification**: `bug`

## What breaks

In a multi-tenant LiteLLM deployment with response caching enabled:
1. Tenant A (e.g. HR or legal team) sends a prompt concerning sensitive corporate data, such as executive payroll or pending contracts.
2. The proxy generates an exact-match cache key based purely on request arguments (model, prompt text, parameters), omitting tenant identity.
3. The upstream model responds, and the sensitive response is saved in the cache.
4. Tenant B (e.g. general staff, contractor, or another tenant organization) sends the same prompt or specifies matching cache parameters.
5. In unpatched LiteLLM, Tenant B matches the same unpartitioned cache key and retrieves Tenant A's cached response directly.
6. The upstream provider is never invoked, leaving no provider audit trail while confidential data leaks across tenant boundaries.

## Wire evidence

- `bug-cross-tenant-cache-leak.json`: Captured unpatched cache output showing identical cache keys across distinct tenants and Tenant B receiving Tenant A's confidential response.
- `control-fixed-tenant-cache-isolation.json`: Captured output under PR #49576 fix showing distinct scoped keys (`tenant-<digest>:...`) and `null` cache hit for Tenant B.
- `control-forged-custom-cache-key-isolated.json`: Captured output verifying that client-supplied `cache_key` is also tagged per tenant, preventing forgery.
- `control-bare-sdk-key-preserved.json`: Captured output confirming that SDK calls without proxy metadata retain identical bare keys.
- `results.json`: 5 of 5 runs reproducing 100% deterministically across all conditions.

## Root cause (if found)

In `litellm/caching/caching.py`:
`Cache.get_cache_key()` hashes only request arguments (`messages`, `model`, `tools`, etc.) and optional caller namespace, omitting tenant credentials (`user_api_key`, `user_api_key_team_id`, `user_api_key_org_id`). While `_get_semantic_cache_tenant_scope()` existed for semantic caching, exact-match caching lacked any tenant scoping hook.

The fix introduces `scope_cache_key()`:
```python
def scope_cache_key(self, cache_key: str, kwargs: Mapping[str, object]) -> str:
    tenant_scope = self._get_tenant_scope(kwargs, self._cache_tenant_scope_fields())
    if not tenant_scope:
        return cache_key
    tenant_digest = hashlib.sha256(tenant_scope.encode()).hexdigest()[:_TENANT_TAG_DIGEST_LENGTH]
    tenant_prefix = f"{_TENANT_TAG_PREFIX}{tenant_digest}:"
    if cache_key.startswith(tenant_prefix):
        return cache_key
    return f"{tenant_prefix}{cache_key}"
```

## Test

- Invariant checker: `response_cache_tenant_isolation` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_exact_cache_cross_tenant_leak_wire_reproduction`
  * `litellm_exact_cache_fixed_tenant_isolation_control`
  * `litellm_exact_cache_forged_custom_key_isolated_control`
  * `litellm_exact_cache_bare_sdk_key_preserved_control`
  * `litellm_exact_cache_matrix_5_runs_reproduce_consistently`
