# 115, LiteLLM key with empty `models: []` bypasses unified access-group restrictions

- **Upstream**: [BerriAI/litellm#46951](https://github.com/BerriAI/litellm/issues/46951), open upstream issue; unmerged PR [BerriAI/litellm#47733](https://github.com/BerriAI/litellm/pull/47733)
- **Tool under test**: LiteLLM `v1.106.6`, commit `cfe0100e1fc` (2026-10-31)
- **Reproduced**: 2026-10-31, local PostgreSQL database, local Redis, pinned checkout commit `cfe0100e1fc`, Python 3.12.13, macOS arm64

## What breaks

When a proxy operator provisions a Virtual Key with `access_group_ids` specifying an Enterprise Unified Access Group (e.g. allowing only `["allowed-model"]`) and leaves the key's native `models` list empty (`models: []`), LiteLLM grants unrestricted access to all models deployed across the proxy. The access group restrictions are completely ignored during authorization.

An unauthorized caller holding the key can query any forbidden model (such as frontier reasoning models or private deployments) and receives HTTP 200 OK. In contrast, an identical control key created with native `models: ["allowed-model"]` correctly returns HTTP 403 Forbidden with `type: "key_model_access_denied"`.

This breaks multi-tenant role-based access control (RBAC): access groups are LiteLLM's documented mechanism to partition model permissions across groups of keys and teams. When keys rely on access groups for authorization, the proxy fails open, defeating model boundaries and quota isolation.

### Bug or not

- Is the expected behavior really the spec? Yes. Official LiteLLM documentation at `https://docs.litellm.ai/docs/proxy/access_groups` defines:
  `Access Groups allow you to group models and restrict keys/teams to only those models in the group.`
  The access group's `access_model_names` is intended as the bounding set for any assigned key or team.
- Have maintainers already ruled on it? Yes. Upstream issue #46951 was accepted as a bug, and PR #47733 was opened by community contributors with regression tests to strictly enforce `access_group_ids` when `models` is empty.
- Is the trigger supported usage? Yes. Creating an access group and generating a key with `access_group_ids` and empty `models: []` is the default and intended usage pattern for access group management.
- Is a real boundary crossed? Yes. A key assigned to an access group granting only `allowed-model` can successfully query `forbidden-model` and receive completions without receiving HTTP 403.
- What fix would a maintainer ship? When `valid_token.access_group_ids` is present and `not key_models`, resolve models from access groups and enforce authorization against that set instead of defaulting to unrestricted proxy-wide access.
- Label: `bug`.

## Wire evidence

- `transcripts/115/wire/access-group-create-request.json`: client request creating an access group restricted to `["allowed-model"]`
- `transcripts/115/wire/access-group-create-response.json`: proxy response returning HTTP 201 with the created `access_group_id`
- `transcripts/115/wire/key-generate-request.json`: client request generating a key with `models: []` and `access_group_ids: ["<ag-id>"]`
- `transcripts/115/wire/key-generate-response.json`: proxy response returning HTTP 200 with the generated key
- `transcripts/115/wire/control-key-forbidden-response.json`: control key (`models: ["allowed-model"]`) requesting `forbidden-model`: HTTP 403 `key_model_access_denied`
- `transcripts/115/wire/trigger-key-forbidden-request.json`: trigger key request to `POST /chat/completions` for `forbidden-model`
- `transcripts/115/wire/trigger-key-forbidden-response.json`: trigger key response returning HTTP 200 OK (`forbidden-model` served, 100% bypass)
- `transcripts/115/wire/trigger-key-allowed-request.json`: trigger key request to `POST /chat/completions` for `allowed-model`
- `transcripts/115/wire/trigger-key-allowed-response.json`: trigger key response returning HTTP 200 OK (`allowed-model` served)

## Differential controls

| Credential | Configured permissions | Model requested | Observed status | Result |
|---|---|---|---|---|
| Control Key | `models: ["allowed-model"]` | `forbidden-model` | HTTP 403 Forbidden | Model restriction enforced |
| Trigger Key | `models: []`, `access_group: ["allowed-model"]` | `forbidden-model` | HTTP 200 OK | Restriction bypassed (100%) |
| Trigger Key | `models: []`, `access_group: ["allowed-model"]` | `allowed-model` | HTTP 200 OK | Allowed model served |

Reproduction rate: 5 of 5 runs reproducible.

## Root cause

In `litellm/proxy/auth/auth_checks.py`:
1. In `can_key_call_model` (lines 5485-5514), model authorization first evaluates the key's native `models` via `can_object_call_model(models=key_models)`.
2. Under LiteLLM conventions in `check_model_access_helper` (lines 4868-4870), `len(models) == 0` evaluates to `all_model_access = True` and returns `True`.
3. Because `can_object_call_model` returns `True` immediately, it never raises a `ProxyException`.
4. As a result, the `except ProxyException:` block that checks `access_group_ids` is never reached. Any key configured with `access_group_ids` and empty `models` inadvertently bypasses all restrictions and can call any model deployed on the proxy.
5. Sibling defect: `can_team_access_model` (lines 5727-5738) exhibits the exact same flaw for teams with `access_group_ids` and empty `models`.

## Upstream status

- Classification: `duplicate-open`
- Upstream issue: [BerriAI/litellm#46951](https://github.com/BerriAI/litellm/issues/46951)
- Upstream pull request: [BerriAI/litellm#47733](https://github.com/BerriAI/litellm/pull/47733)
- Date checked: 2026-10-31

## Test

Harness conformance test in `crates/harness/tests/conformance.rs`:
- `litellm_access_group_empty_models_forbidden_model_bypassed_violation`: asserts that a key bound to an access group allowing only `allowed-model` returning HTTP 200 for `forbidden-model` is caught as a violation.
- `litellm_access_group_control_key_rejected`: asserts that the control key returning HTTP 403 for `forbidden-model` is conformant.
- `litellm_access_group_allowed_model_served`: asserts that the trigger key returning HTTP 200 for `allowed-model` is conformant.
