# 076, LiteLLM managed vector stores expose server credentials to internal users

## Claim

An authenticated `internal_user` who is allowed to manage and search vector
stores can attach an admin-defined named credential to a user-owned vector
store, pair it with a caller-controlled `api_base`, and make LiteLLM send the
server credential to the caller's endpoint.

- Upstream project: [BerriAI/litellm](https://github.com/BerriAI/litellm)
- Cited upstream issue or report: private security report not yet submitted;
  related published advisory
  [GHSA-3cv6-jpf6-8222](https://github.com/BerriAI/litellm/security/advisories/GHSA-3cv6-jpf6-8222)
  covers a different request-body routing path
- Tested release or commit: `v1.100.0`,
  `e4f25265704e2b2c6cf6e81be2e4c5cffff896f4`
- Client dialect and endpoint: LiteLLM management API
  `POST /vector_store/new`, then OpenAI-compatible
  `POST /v1/vector_stores/{id}/search`
- Backend dialect and provider or capture upstream: OpenAI vector-store search
  sent to a deterministic raw HTTP capture server on loopback
- Model, if relevant: none; vector-store search does not invoke a language model
- Relevant configuration: PostgreSQL, `master_key`, a config-defined OpenAI
  credential named `prod-openai`, and an `internal_user` virtual key explicitly
  granted `/vector_store/new`, `/v1/model/info`, and `openai_routes`

## Gate 1: correctness

### Exact reproduction

The reproduction runner starts a disposable PostgreSQL 17 server, LiteLLM's
real proxy CLI, and a raw loopback HTTP capture server. All credentials are
random synthetic canaries generated at runtime. It exercises only public HTTP
endpoints and writes evidence only after replacing every canary with a named
marker.

```text
git clone https://github.com/BerriAI/litellm.git /path/to/litellm
cd /path/to/litellm
git checkout e4f25265704e2b2c6cf6e81be2e4c5cffff896f4
uv sync --frozen --extra proxy --extra extra_proxy

# Locate the executable fetched with prisma-python without printing secrets.
KAIRO_PRISMA_ENGINE=$(.venv/bin/python - <<'PY'
from pathlib import Path
from prisma.client import BINARY_PATHS

for expected in BINARY_PATHS.query_engine.values():
    expected = Path(expected)
    installed = expected.parent.parent / "@prisma" / "engines" / expected.name
    if installed.is_file():
        print(installed)
        break
else:
    raise SystemExit("Prisma query engine not found")
PY
)

cd /path/to/kairo
python3 transcripts/076/reproduce.py \
  --litellm-source /path/to/litellm \
  --python /path/to/litellm/.venv/bin/python \
  --prisma-query-engine "$KAIRO_PRISMA_ENGINE" \
  --postgres-bin "$(brew --prefix postgresql@17)/bin" \
  --output-dir /tmp/kairo-076-review
```

- Expected behavior: a credential owned and configured by the proxy operator
  must not leave the proxy in a request whose destination is selected by an
  internal user. A user-owned vector store may use a caller-owned key, but must
  not gain the value behind an admin-defined credential reference.
- Observed behavior: all three searches of the named-credential store sent
  `Authorization: Bearer [SERVER_PROVIDER_CREDENTIAL]` to the caller-selected
  capture endpoint and returned HTTP 200.
- Raw request evidence: `transcripts/076/live/exploit.jsonl`, field
  `client_request_raw`, contains the literal HTTP request for each search.
- Raw response evidence: `transcripts/076/live/exploit.jsonl`, field
  `client_response_raw`, contains each literal HTTP 200 response.
- Forwarded-request evidence: `transcripts/076/live/exploit.jsonl`, fields
  `upstream_request_raw` and `upstream_response_raw`, contain the literal bytes
  received and returned by the capture endpoint.
- Reproduction rate: server credential reached the caller endpoint 3/3;
  successful client responses 3/3.
- Smallest isolated trigger: setting `litellm_credential_name` on the
  internal-user-owned vector-store row while keeping its `api_base` under the
  caller's control. Removing the named credential and supplying a caller-owned
  `api_key` changes only the credential source and eliminates the disclosure.

The setup evidence in `transcripts/076/live/setup.jsonl` also proves that the
same internal user discovered only the credential reference through
`/v1/model/info`. The server credential value does not appear in that response.

### Control

The runner creates a second vector store with the same owner, provider,
destination, route, search query, and request count. It removes only
`litellm_credential_name` and gives that store a caller-owned synthetic key.

```text
exploit: litellm_credential_name = prod-openai, api_base = capture endpoint
control: api_key = caller-owned canary,          api_base = capture endpoint
```

- Control result: the server credential reached the endpoint 0/3, the
  caller-owned credential reached it 3/3, and all client responses were HTTP
  200. Literal bytes are in `transcripts/076/live/control.jsonl`.
- Why this attributes the failure to the claimed layer: the destination,
  caller, public endpoint, provider adapter, request body, database, and proxy
  process are identical. Only the stored credential source changes. The raw
  upstream Authorization value changes with it.

### False-positive checks

- [x] Tested the exact cited behavior, not a similar symptom.
- [x] Pinned and reported the target version or commit.
- [x] Ruled out bad configuration and malformed input.
- [x] Ruled out model nondeterminism or reported why it is irrelevant.
- [x] Confirmed the failure is not created only by the mock or harness.
- [x] Sanitized all recorded evidence.

The capture upstream is appropriate here because the claim concerns exactly
what the gateway forwards. LiteLLM itself performed authentication, ownership
checks, database persistence, named-credential resolution, provider request
construction, and the outbound HTTP request. The capture server only returned
a deterministic valid OpenAI-shaped empty search result. No provider behavior
or model output is part of the claim.

## Root cause

The authenticated management route calls `check_feature_access_for_user`, then
passes both caller-controlled `litellm_params` and caller-selected
`litellm_credential_name` into the new row with the internal user's `user_id`
and `team_id`. There is no authorization check for access to the named
credential:

- [`management_endpoints.py` lines 500-550](https://github.com/BerriAI/litellm/blob/e4f25265704e2b2c6cf6e81be2e4c5cffff896f4/litellm/proxy/vector_store_endpoints/management_endpoints.py#L500-L550)
- [`create_vector_store_in_db` stores the credential reference and parameters](https://github.com/BerriAI/litellm/blob/e4f25265704e2b2c6cf6e81be2e4c5cffff896f4/litellm/proxy/vector_store_endpoints/management_endpoints.py#L447-L484)

On search, LiteLLM verifies that the same internal user owns the vector store.
It then reloads both the stored credential reference and the stored
`litellm_params`, including `api_base`, into request processing. The OpenAI
adapter resolves the named credential and sends it to that base URL:

- [`endpoints.py` lines 51-94](https://github.com/BerriAI/litellm/blob/e4f25265704e2b2c6cf6e81be2e4c5cffff896f4/litellm/proxy/vector_store_endpoints/endpoints.py#L51-L94)
- [`vector_store_search` uses the hydrated row](https://github.com/BerriAI/litellm/blob/e4f25265704e2b2c6cf6e81be2e4c5cffff896f4/litellm/proxy/vector_store_endpoints/endpoints.py#L97-L160)

Ownership checks protect one user's vector store from another user. They do not
establish that the row's owner is authorized to use an operator-owned named
credential.

## Gate 2: usefulness

### Who gets bitten

- Affected user or customer: a self-hosted LiteLLM operator who enables the
  built-in vector-store UI or API for internal users and keeps provider keys in
  LiteLLM's reusable credential registry.
- Real workflow: an operator stores one provider credential, references it from
  model deployments, and lets authenticated internal users create and search
  their own vector stores. Credential names are visible from the documented
  model-info route, while values are intended to remain secret.
- Preconditions and likely frequency: the attacker needs a valid internal-user
  key with the vector-store management and OpenAI route grants used by the
  product's route ACL. The proxy must have at least one reusable named
  credential. Once a store is registered, disclosure occurred on every search,
  measured 3/3.

### Observable consequence

- User action: an internal user creates a user-owned OpenAI-compatible vector
  store using the visible credential name and a destination the user controls,
  then searches it.
- Wire-level defect: LiteLLM sends the operator's provider bearer credential to
  that destination.
- End-user or agent-level failure: the internal user obtains a provider
  credential outside their LiteLLM role and virtual-key scope. It can then be
  used directly against the provider subject to the provider key's permissions.
- Consumer-boundary demonstration or transcript: the caller-controlled capture
  endpoint received the server credential marker in the literal Authorization
  header in all three exploit requests. It received no server credential in
  any control request.
- Measured impact: one complete operator credential crossed into the lower
  privileged caller's endpoint 3/3 with authentication enabled.
- Inferred impact, clearly labeled: provider-side use of the recovered key could
  consume budget or access provider resources allowed to that key. This was not
  attempted because the reproduction uses synthetic canaries only.

### Bug or not

- Expected behavior confirmed as intended spec, not a stale doc line: LiteLLM's
  own `disable_vector_stores_for_internal_users` setting defaults to `false` and
  says internal users can access vector-store management and its UI. Its RBAC
  tests construct an `INTERNAL_USER`, require access when the feature is not
  disabled, and require denial only when the setting is enabled. The dashboard
  calls `/vector_store/new`. Separately, model-info returns credential names but
  credential values are redacted. These code, test, and UI behaviors establish
  that vector-store management is supported while reusable credential values
  remain privileged.
- Maintainer ruling searched and result: no issue, PR, commit, code comment, or
  denylist was found that permits an internal user to bind an arbitrary
  operator-owned credential to a caller-selected origin. Recent hardening
  commits instead treat vector-store configuration as server-side and block
  caller-selected embedding parameters on query surfaces.
- Trigger is supported usage with default or recommended settings: master-key
  authentication is enabled, the database and named credential use documented
  configuration, internal-user vector stores are enabled by default, and the
  route grant uses LiteLLM's documented `allowed_routes` mechanism. No security
  control is disabled.
- Boundary crossed: the `internal_user` and its virtual key are allowed to use
  LiteLLM routes, not to read the operator's reusable provider credential. The
  capture endpoint controlled by that user receives the credential value. This
  is the same P2 class LiteLLM's security policy defines as authenticated action
  beyond intended permissions.
- Fix a maintainer would ship, in one sentence: reject a non-admin vector-store
  create or update that names a credential the caller is not authorized to use,
  and prevent any privileged credential from being combined with a
  caller-controlled provider origin.
- Label: `bug`

## Gate 3: upstream status

- Date checked: 2026-09-10
- Upstream version checked: latest stable `v1.100.0`, prerelease
  `v1.101.0-rc.2`, development tag `v1.102.0-dev.1`, and
  `litellm_internal_staging` commit
  `e5da59336dacf9c69a07c1cb21dae2b9f4f828a4`
- Search terms used: `vector store litellm_credential_name api_base credential`,
  `vector store named credential leak`, `vector store credential exfiltration`,
  `vector_store/new credential`, `vector_stores search api_base`,
  `provider credential SSRF`, `litellm_credential_name persistence`, and the
  exact route and field names
- Issues searched: open and closed GitHub issues, including
  [#37053](https://github.com/BerriAI/litellm/issues/37053),
  [#16303](https://github.com/BerriAI/litellm/issues/16303), and
  [#35599](https://github.com/BerriAI/litellm/issues/35599)
- Pull requests searched: open, closed, merged, and draft pull requests,
  including [#38934](https://github.com/BerriAI/litellm/pull/38934)
- Releases, changelog, and documentation searched:
  [`v1.100.0`](https://github.com/BerriAI/litellm/releases/tag/v1.100.0), the
  current releases list, vector-store docs and schemas, route ACL docs, and the
  [security policy](https://github.com/BerriAI/litellm/security/policy)
- Relevant commits searched:
  [`babe7816ada8d622be993b542fc2512037d2466f`](https://github.com/BerriAI/litellm/commit/babe7816ada8d622be993b542fc2512037d2466f)
  and
  [`8b0441a628c01f0cd6caa10176ae887c06d75fa7`](https://github.com/BerriAI/litellm/commit/8b0441a628c01f0cd6caa10176ae887c06d75fa7)
- Matching links: the items above and
  [GHSA-3cv6-jpf6-8222](https://github.com/BerriAI/litellm/security/advisories/GHSA-3cv6-jpf6-8222)

Classification:

- [x] Novel
- [ ] Duplicate, open and still reproducible
- [ ] Fixed on current release
- [ ] Regression
- [ ] Documented behavior
- [ ] Discussed upstream without a dedicated ticket
- [ ] Incomplete, current upstream state could not be verified

The published advisory is adjacent but not a duplicate. It covered routing and
credential parameters supplied in each LLM request body and was patched in the
1.96.x line. This finding uses a different authenticated management route to
persist the combination first. The later search body contains only `query`, so
the patched request-body denylist has nothing to reject.

Issue #37053 described the direct request-body path under client-side auth
opt-ins and was closed because it should have been privately reported. Issue
#16303 covered cross-user vector-store authorization and was fixed by ownership
checks; this finding passes those checks because the attacker owns the store.
Issue #35599 concerns failure to resolve registry credentials. Draft PR #38934
keeps resolved RAG credentials out of database persistence for database readers;
it does not authorize who may bind the remaining named reference or where that
credential may be sent.

Static inspection on 2026-09-10 found the same create and search data flow in
`v1.101.0-rc.2`, `v1.102.0-dev.1`, and current staging. Current staging still
stores `vector_store.get("litellm_credential_name")` for the authenticated
caller and later builds outbound request data from that credential reference
plus the row's `litellm_params`:

- [`management_endpoints.py` at current staging](https://github.com/BerriAI/litellm/blob/e5da59336dacf9c69a07c1cb21dae2b9f4f828a4/litellm/proxy/vector_store_endpoints/management_endpoints.py#L245-L294)
- [`endpoints.py` at current staging](https://github.com/BerriAI/litellm/blob/e5da59336dacf9c69a07c1cb21dae2b9f4f828a4/litellm/proxy/vector_store_endpoints/endpoints.py#L52-L87)

Those newer revisions were inspected statically, not dynamically reproduced.
The dynamic 3/3 result is limited to the latest stable release `v1.100.0`.

## Frozen invariant

- Issue writeup: this file.
- Checker added or updated: `outbound_request_omits_secret` in
  `crates/harness/src/checks.rs`.
- Conformance test added or updated:
  `litellm_vector_store_exposes_server_credential_to_caller_endpoint` and
  `litellm_vector_store_caller_owned_key_control_omits_server_credential` in
  `crates/harness/tests/conformance.rs`.
- Why the checker tests the invariant rather than one implementation detail:
  it validates a complete raw HTTP request and scans the entire request for the
  privileged credential marker. It does not depend on LiteLLM's internal class,
  a specific provider header name, or whether a provider moves authentication
  into the request target or body. Empty and malformed captures fail closed.

## Validation

| Check | Command | Result |
|---|---|---|
| Reproduction | `python3 transcripts/076/reproduce.py ... --output-dir /tmp/kairo-076-review` | server credential 3/3; control 0/3; caller control credential 3/3 |
| Control | same command, built into runner | HTTP 200 3/3; server credential absent 3/3 |
| Harness | `cargo test --workspace` | 28 unit and 136 conformance tests passed |
| Formatting | `cargo fmt --all -- --check` | passed |
| Lint | `cargo clippy --workspace --all-targets -- -D warnings` | passed |
| README counts | `python3 tools/update-readme-counts.py --check` | passed, 53 folders and 164 Rust tests |
| Independent review | `.github/agents/kairo-reproduction-reviewer.agent.md` | ACCEPT; exploit 3/3, server credential control 0/3, all checks passed |

## Security and scope

- [x] No API key, credential, private prompt, or unsanitized response is committed.
- [x] Only environment variable names appear in commands and documentation.
- [x] The pull request contains one finding.
- [x] Unrelated generated files and local state are excluded.

All four values are runtime-generated synthetic canaries. Before writing any
file, the runner replaces them with `[MASTER_KEY]`, `[INTERNAL_USER_KEY]`,
`[SERVER_PROVIDER_CREDENTIAL]`, and `[CALLER_CONTROL_CREDENTIAL]`, then rejects
the output if any original canary remains. The proxy process receives an
allowlisted environment and does not inherit provider credentials from the
invoking shell.

LiteLLM's security policy requires private GitHub reporting and a terminal or
video recording of the live exploit. This finding must not be filed as a public
upstream issue. A sanitized terminal recording has been prepared for upstream
reporting. No private advisory or email has been sent.

## Author verdict

- Correctness: PASS
- Usefulness: PASS
- Upstream status: PASS
- Overall: ACCEPT

All three gates, repository checks, and the independent reproduction review
pass.

## Independent review

On 2026-09-10, an independent reviewer followed
`.github/agents/kairo-reproduction-reviewer.agent.md`, reran the pinned real
LiteLLM CLI with PostgreSQL and Prisma, and tried to falsify the claim.

- Correctness: PASS. The server credential reached the caller endpoint 3/3;
  the matched caller-owned-key control received it 0/3 and received the caller
  credential 3/3.
- Usefulness: PASS. The reviewer confirmed that an authenticated
  `internal_user` crossed the operator credential boundary.
- Upstream status: PASS. Current primary upstream sources were searched and the
  persisted managed-vector-store path was classified `novel`.
- Repository checks: PASS. Python tests, Rust unit and conformance tests,
  formatting, clippy, README counts, diff checks, and the targeted credential
  scan all passed.
- Repository state: clean. The reviewer made no repository changes.
- Final reviewer verdict: ACCEPT.
