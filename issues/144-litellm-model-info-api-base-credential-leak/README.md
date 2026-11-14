# 144, LiteLLM /model/info returns the deployment's api_base unmasked to internal users: hardening, not a bug

**Verdict: false positive as a bug. Relabeled `hardening` on 2026-11-14 and kept
as an honest negative. The bytes reproduce; the trigger is not supported usage.
Reported upstream as a hardening request:
[BerriAI/litellm#51810](https://github.com/BerriAI/litellm/issues/51810). See
"Why this is not a bug".**

- **Upstream**: [BerriAI/litellm](https://github.com/BerriAI/litellm). No upstream
  issue or PR covers it; the sibling disclosure class on `/health` was reported
  and fixed by the maintainers (api_base became admin-only there), and the proxy
  itself strips query parameters from `api_base` in its own response headers
  because of "risk of leaking sensitive info".
- **Recorded as**: `novel` hardening request, with wire evidence, controls, and a
  fixed-build control. Filed upstream as
  [BerriAI/litellm#51810](https://github.com/BerriAI/litellm/issues/51810)
  (feature request, 2026-11-14).
- **Tool under test**: LiteLLM at main `6abacd329b70dc668f69c705eb7118fbd639cd96`
  (upstream default branch, 2026-11-13), run through the real proxy CLI with a
  real PostgreSQL 16 database (Docker, scratch) and a local deterministic
  OpenAI-compatible upstream. Python 3.12.13.
- **Reproduced**: 2026-11-14, local. 5 of 5 over HTTP.
- **Label**: `hardening` after review (the original draft said `bug`).

## What breaks

`remove_sensitive_info_from_deployment` (the sanitizer every model-info listing
runs) pops `api_key`, `client_secret`, `vertex_credentials` and the AWS fields,
and then masks any remaining value whose key name matches a sensitive pattern
(password/secret/key/token/auth/credential/...). The pattern list has no
`base` segment, so `litellm_params.api_base` passes through unmasked. A
deployment whose base URL carries its credential - Azure Storage SAS
(`?sig=...`), Gemini-style `?key=...`, Databricks workspace tokens, self-hosted
gateways with query tokens - hands that credential to every authenticated
internal user that can see the model.

The proxy's own invariant says api_base query strings are credentials: the
response headers it builds strip them (`"x-litellm-model-api-base":
api_base.split("?")[0]` with the comment "don't include query params, risk of
leaking sensitive info"), and the log redactor carries Azure SAS (`(?<=[?&])sig=`)
and `key=` URL patterns. On `/health` the same family was already fixed so that
api_base is admin-only. The model-info listings are the remaining hole.

```text
Attacker = any authenticated internal user (a plain virtual key holder)
  -> GET /model/info  (and /v1/model/info, /v2/model/info)
  <- 200 {"data":[{"model_name":"cred-model",
        "litellm_params":{"api_base":"http://127.0.0.1:9555/v1?cred=CANARY-SECRET-ABCD",
        ...  "api_key" is gone, the URL credential is not ...
  -> calls the provider directly with the stolen base URL, bypassing the
     proxy's keys, budgets, guardrails and logging
```

Affected deployments: any `api_base` embedding a credential in its URL. Internal
users are the standard tenant role; the dashboard shows them the model table via
these same endpoints.

## Cells

Every cell is 5 of 5 over HTTP against the real proxy on the pinned commit.

| Cell | Caller | Route | Result |
|---|---|---|---|
| attack (x5) | plain internal user key | `GET /model/info` | 200, raw `?cred=CANARY-SECRET-ABCD` in the response |
| attack, other listings | plain internal user key | `GET /v1/model/info`, `GET /v2/model/info` | 200, same leak |
| control A (field guard works) | plain internal user key | same responses | `api_key` is popped (`None`) while `api_base` leaks: the sanitizer runs and misses only the URL |
| control B (fixed build) | unit level | `remove_sensitive_info_from_deployment` + masker with the `base` pattern | `api_base` masked to `http****...****ABCD`, leak gone |

## Wire evidence

All of `transcripts/144/`, sanitized (the scratch keys are replaced with marked
placeholders; the credential in the URL is a canary, not a real secret).

- `internal_user_model_info.http`, `internal_user_v1_model_info.http`,
  `internal_user_v2_model_info.http`: the unmasked `api_base` with the canary
  credential in the internal-user response, `api_key` absent.
- `admin_control_same_shape.http`: the proxy admin receives the same body shape
  (admins are entitled to deployment credentials; the boundary that must hold
  is the internal user).
- `api_base_query_strip_invariant.md`: the in-repo code that strips api_base
  query strings from response headers with the "risk of leaking sensitive info"
  comment, and the masker's pattern list that has no `base` segment.
- `repro/model_info_leak_repro.py`: N/N reproduction plus the fixed-build
  control (masker + `base` pattern), runnable against a clean checkout.

## Root cause

LiteLLM main `6abacd329b7`:

- `litellm/proxy/common_utils/openai_endpoint_utils.py:18`
  (`remove_sensitive_info_from_deployment`): pops credential fields, then calls
  `SENSITIVE_DATA_MASKER.mask_dict` with the global pattern list.
- `litellm/litellm_core_utils/sensitive_data_masker.py:11-30`
  (`_DEFAULT_SENSITIVE_PATTERNS`): no `base` segment, so `api_base` is never
  considered sensitive.
- `litellm/proxy/proxy_server.py:18587-18610` (`/model/info`, `/v1/model/info`)
  and the `/v2/model/info` handler return the sanitized-but-not-enough dict to
  non-admin callers.
- The invariant the codebase states elsewhere: response headers strip api_base
  query params (`common_request_processing.py`, "don't include query params,
  risk of leaking sensitive info") and the log redactor matches `?sig=` / `key=`
  URL credentials (`litellm/litellm_core_utils/secret_redaction.py`).

## Bug or not

Original analysis, kept for the record. The "supported usage" and "Label"
bullets are superseded by "Why this is not a bug" below: no LiteLLM doc or
example puts a `?key=` credential in `api_base`.

- **Expected behavior**: api_key masking on these endpoints is intended and
  present; api_base visibility for internal users is a deliberate choice for the
  PATH only (the dashboard shows deployments' base URLs to admins). Embedding a
  credential in the URL is a common operator pattern (SAS tokens, query keys)
  that the proxy acknowledges as sensitive everywhere else it appears.
- **Maintainer ruling checked**: none found upstream; the `/health` sibling was
  fixed by making api_base admin-only, which supports treating model-info
  listings as a disclosure to close, not intended behavior.
- **Trigger is supported usage**: internal users reading model info is a
  documented default-tier capability; deployments with credentials in api_base
  URLs are common (the proxy's own docs use `?key=` style for Gemini-era
  deployments).
- **Boundary crossed**: the internal user (must not see deployment credentials)
  receives a provider credential and can bypass every proxy control.
- **Fix a maintainer would ship, in one sentence**: in
  `remove_sensitive_info_from_deployment`, mask `api_base` the way `api_key` is
  handled (first-4/last-4 via the masker with a `base` pattern, or strip the
  query string) for non-admin callers. Reverse-direction risk: the dashboard's
  admin model editor reads these endpoints to prefill forms, so the fix must
  keep full api_base visibility for proxy admins (the
  `is_full_admin` branch already exists in `_redact_general_setting_value` for
  the settings surface).
- **Label**: `hardening` after review (the original draft said `bug`).

## Why this is not a bug

The independent review reproduced the bytes (5/5 on each of the three routes)
and first accepted the finding. A follow-up check of the bug-or-not gate on
2026-11-14 reversed that.

- The trigger is not supported usage. It needs an operator to put a credential
  inside `api_base`, a non-secret field. No LiteLLM documentation, example, or
  config in the repository does that (searched `api_base` with `?key=`, `?sig=`,
  `?code=`, `?token=`, and `user:pass@` userinfo). `AGENTS.md` classes a secret in
  a non-secret field as operator misuse, and LiteLLM's `SECURITY.md` lists
  misconfiguration-dependent attacks as known non-issues.
- The tested layout does not even work for traffic. With the `openai/` provider,
  a query string in `api_base` breaks the request URL: the review's capture
  upstream received `POST /v1/?cred=CANARY-SECRET-ABCDchat/completions` and the
  gateway call returned 400. An operator would not run this configuration.
- What remains is a reasonable hardening ask. LiteLLM already hides `api_base`
  from non-admins on `/health`
  ([#40765](https://github.com/BerriAI/litellm/pull/40765),
  `ADMIN_ONLY_HEALTH_DISPLAY_PARAMS`) and strips its query string from the
  `x-litellm-model-api-base` header. Applying the same rule to the model-info
  listings is consistent, so it was filed upstream as a feature request, not a
  vulnerability report.
- Severity if it were reached: authenticated (LiteLLM P2), minor information
  disclosure (LiteLLM's Low tier), no bounty eligibility.

What would reopen it as a bug: a documented or default LiteLLM setup that
places a credential in `api_base`, then shown leaking to a non-admin caller.

## Upstream status

- Date checked: 2026-11-14.
- Upstream release, commit, or hosted deployment checked: default branch
  `6abacd329b7` (2026-11-13).
- Search terms used: "model/info api_base", "api_base leak", "api_base mask",
  plus the /health sibling's advisory history.
- Issues and PRs searched: none cover the model-info api_base disclosure.
- Classification: **novel** as a hardening request, filed as
  [BerriAI/litellm#51810](https://github.com/BerriAI/litellm/issues/51810) on
  2026-11-14.

## Test

Not added to the Rust harness (the invariant sits behind the proxy's model-info
surface and a database-backed deployment row). The scripted reproduction with
the fixed-build control is committed at `issues/144-*/repro/`. The invariant,
not the bug: "credentials embedded in `api_base` must not leave the admin
boundary in any non-admin model-info listing."

## Reproduction (commands)

```text
# in a clean checkout of litellm at 6abacd329b7 (venv, prisma client generated):
#   docker run -d --name kairo144-db -e POSTGRES_PASSWORD=<scratch> -e POSTGRES_DB=litellm -p 127.0.0.1:55432:5432 postgres:16-alpine
#   env LITELLM_MASTER_KEY=$LITELLM_MASTER_KEY DATABASE_URL=$LITELLM_DATABASE_URL \
#       STORE_MODEL_IN_DB=True .venv-hunt/bin/litellm --config repro/config.yaml --port 4100
#   .venv-hunt/bin/python repro/model_info_leak_repro.py   # starts the mock upstream, seeds
#       a deployment whose api_base embeds ?cred=CANARY..., mints an internal user key,
#       and runs the 5/5 attack, the field-guard control and the fixed-build control
# Secret env var names only: LITELLM_MASTER_KEY, LITELLM_DATABASE_URL.
```

Expected: internal user sees `api_key` removed but `api_base` raw. Observed: 5
of 5. Smallest isolated trigger: `remove_sensitive_info_from_deployment` letting
`litellm_params.api_base` through the masker.
