# 115, LiteLLM key with empty models is additive grant by design, falsified as a bug

- **Upstream**: [BerriAI/litellm#46951](https://github.com/BerriAI/litellm/issues/46951); unmerged PR [BerriAI/litellm#47733](https://github.com/BerriAI/litellm/pull/47733); opt-in toggle PR [BerriAI/litellm#47827](https://github.com/BerriAI/litellm/pull/47827)
- **Tool under test**: LiteLLM `v1.106.6`, commit `cfe0100e1fc` (2026-10-31)
- **Reproduced**: 2026-10-31, local PostgreSQL database, local Redis, pinned checkout commit `cfe0100e1fc`, Python 3.12.13, macOS arm64

## What was evaluated

When a proxy operator provisions a Virtual Key with `access_group_ids` specifying an Enterprise Unified Access Group (e.g. allowing only `["allowed-model"]`) and leaves the key's native `models` list empty (`models: []`), LiteLLM allows requests to models outside the group (e.g. `forbidden-model` returns HTTP 200 OK).

The initial claim characterized this as an authorization bypass defect under the premise that an access group is a restrictive boundary that should constrain keys assigned to it.

## Why it is falsified as a bug (`feature-request`)

Independent review rejected the finding as a bug. In LiteLLM's security architecture:
1. `models: []` (or `None`) is intentionally defined across the codebase as "allow access to all models" (`empty_models_allow_everything`).
2. Access groups are designed as additive model grants (fallback permissions), not restrictive masks. If a key has access to all models via `models: []`, assigning an access group does not restrict that key.
3. The UI in `ui/litellm-dashboard/src/components/create_key_button.tsx` (line 954) explicitly documents that an empty models selection allows all models.
4. LiteLLM provides a documented sentinel: setting `models: ["no-default-models"]` creates a key with zero baseline model access, thereby restricting it strictly to the models in its assigned `access_group_ids`.
5. Testing the sentinel key (`models: ["no-default-models"]`, `access_group_ids: [ag_id]`) confirms that LiteLLM properly enforces the access group: calls to `forbidden-model` return HTTP 403 `key_model_access_denied`, while calls to `allowed-model` return HTTP 200 OK (5 of 5 runs).
6. Upstream PR #47827 introduced `enforce_access_groups_on_empty_models` as an opt-in toggle rather than changing the default behavior, confirming that existing default semantics are preserved for backward compatibility. Changing the default behavior is a feature request, not a bug.

### Bug or not

- Is the expected behavior really the spec? No. The spec treats `models: []` as unrestricted all-model access, and access groups as additive grants.
- Have maintainers already ruled on it? Yes. PR #47827 established an opt-in toggle `enforce_access_groups_on_empty_models` rather than changing default semantics.
- Is the trigger supported usage? Yes, but relying on `models: []` to restrict access is operator misunderstanding; the documented sentinel for access group only keys is `models: ["no-default-models"]`.
- Is a real boundary crossed? No. With `models: []`, the key has unrestricted access by design. When properly provisioned with `models: ["no-default-models"]`, the boundary is enforced (HTTP 403).
- What fix would a maintainer ship? A documentation clarification or the opt-in flag already provided in PR #47827.
- Label: `feature-request`.

## Wire evidence

- `transcripts/115/wire/access-group-create-request.json`: client request creating an access group restricted to `["allowed-model"]`
- `transcripts/115/wire/access-group-create-response.json`: proxy response returning HTTP 201 with created `access_group_id`
- `transcripts/115/wire/key-generate-request.json`: client request generating trigger key with `models: []` and `access_group_ids: ["<ag-id>"]`
- `transcripts/115/wire/key-generate-response.json`: proxy response returning HTTP 200 with generated key
- `transcripts/115/wire/nodefault-key-generate-request.json`: client request generating sentinel key with `models: ["no-default-models"]` and `access_group_ids: ["<ag-id>"]`
- `transcripts/115/wire/nodefault-key-generate-response.json`: proxy response returning HTTP 200 with sentinel key
- `transcripts/115/wire/control-key-forbidden-response.json`: native control key (`models: ["allowed-model"]`) requesting `forbidden-model`: HTTP 403 `key_model_access_denied`
- `transcripts/115/wire/nodefault-key-forbidden-response.json`: sentinel key requesting `forbidden-model`: HTTP 403 `key_model_access_denied` (restriction enforced)
- `transcripts/115/wire/nodefault-key-allowed-response.json`: sentinel key requesting `allowed-model`: HTTP 200 OK (access group grant honored)
- `transcripts/115/wire/trigger-key-forbidden-request.json`: trigger key request to `POST /chat/completions` for `forbidden-model`
- `transcripts/115/wire/trigger-key-forbidden-response.json`: trigger key response returning HTTP 200 OK (empty models allows all models)
- `transcripts/115/wire/trigger-key-allowed-request.json`: trigger key request to `POST /chat/completions` for `allowed-model`
- `transcripts/115/wire/trigger-key-allowed-response.json`: trigger key response returning HTTP 200 OK
- `transcripts/115/wire/results.json`: 5 of 5 runs summary

## Differential controls

| Credential | Configured permissions | Model requested | Observed status | Result |
|---|---|---|---|---|
| Control Key | `models: ["allowed-model"]` | `forbidden-model` | HTTP 403 Forbidden | Model restriction enforced |
| Sentinel Key | `models: ["no-default-models"]`, `access_group: ["allowed-model"]` | `forbidden-model` | HTTP 403 Forbidden | Access group restriction enforced |
| Sentinel Key | `models: ["no-default-models"]`, `access_group: ["allowed-model"]` | `allowed-model` | HTTP 200 OK | Access group allowed model served |
| Trigger Key | `models: []`, `access_group: ["allowed-model"]` | `forbidden-model` | HTTP 200 OK | Allowed by design (`empty_models_allow_everything`) |
| Trigger Key | `models: []`, `access_group: ["allowed-model"]` | `allowed-model` | HTTP 200 OK | Allowed model served |

Reproduction rate: 5 of 5 runs reproducible.

## Upstream status

- Classification: `discussed-no-ticket`
- Upstream issue: [BerriAI/litellm#46951](https://github.com/BerriAI/litellm/issues/46951)
- Upstream pull requests: [BerriAI/litellm#47733](https://github.com/BerriAI/litellm/pull/47733), [BerriAI/litellm#47827](https://github.com/BerriAI/litellm/pull/47827)
- Date checked: 2026-10-31

## Test

Harness conformance tests in `crates/harness/tests/conformance.rs`:
- `litellm_access_group_empty_models_forbidden_model_bypassed_violation`: records that an expectation of restrictive bounding on `models: []` produces a violation against the current implementation.
- `litellm_access_group_control_key_rejected`: verifies that native model allowlists return HTTP 403 for forbidden models.
- `litellm_access_group_nodefault_sentinel_forbidden_model_rejected`: verifies that the `models: ["no-default-models"]` sentinel properly enforces HTTP 403 rejection for models outside the access group.
- `litellm_access_group_nodefault_sentinel_allowed_model_served`: verifies that the sentinel key successfully serves models granted by the access group.
- `litellm_access_group_allowed_model_served`: verifies that the trigger key serves allowed models with HTTP 200 OK.
