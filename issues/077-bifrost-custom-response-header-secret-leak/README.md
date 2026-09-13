# Finding

## Claim

Bifrost filters provider response headers through a fixed denylist of 28 exact
names. It does not consult `schemas.IsSensitiveHeader`, the classifier the same
codebase already uses to recognize credential-bearing header names. Any header
whose name that classifier calls a credential, but which is absent from the fixed
list, is copied to the inference caller in both the HTTP response header and
`extra_fields.provider_response_headers`.

Demonstrated consequence: when a provider or an intermediate proxy reflects an
administrator-configured `network_config.extra_headers` credential, a
provider-scoped virtual-key caller receives that credential, even though the same
key is refused by the management API that would otherwise expose it.

- Upstream project: [maximhq/bifrost](https://github.com/maximhq/bifrost)
- Cited upstream issue or report: [#3954](https://github.com/maximhq/bifrost/issues/3954), fixed incompletely by [#3955](https://github.com/maximhq/bifrost/pull/3955)
- Tested release or commit: `dev` at `44a562431ee0463cb1afe0e5833cde07d7921701`, core 1.8.6, binary version `1.8.6-44a562431e`
- Client dialect and endpoint: OpenAI Responses, `POST /v1/responses`
- Backend dialect and provider or capture upstream: OpenAI Responses, documented custom OpenAI-compatible provider, deterministic local capture upstream
- Model, if relevant: `mock-model`; model behavior is irrelevant
- Relevant configuration: `client.enforce_auth_on_inference=true`, `governance.auth_config.is_enabled=true`, one provider-scoped virtual key, and provider `network_config.extra_headers.X-Provider-Secret`

### The asymmetry, from source alone

Two functions in the same module disagree about what a credential is.

`core/providers/utils/utils.go:923` defines the response filter as a fixed map,
documented as covering "transport-level headers that don't apply when re-serving
the response", plus the four credential names [#3955](https://github.com/maximhq/bifrost/pull/3955)
appended. Three extractors share that map and nothing else:

| Line | Function | Used by |
|---|---|---|
| 966 | `ExtractProviderResponseHeaders` | fasthttp providers, ordinary inference |
| 992 | `ExtractPassthroughProviderResponseHeaders` | passthrough routes, keeps `content-type` |
| 1017 | `ExtractProviderResponseHeadersFromHTTP` | net/http providers such as Bedrock |

`core/schemas/trace.go:1026` defines `IsSensitiveHeader`, which returns true for
any name containing `api-key`, `authorization`, `secret`, `assertion`, or `jwt`,
any name prefixed `cf-access-` or `x-amzn-oidc-`, and any name suffixed `-token`
or `_token`.

`IsSensitiveHeader("X-Provider-Secret")` is therefore true, and
`providerResponseFilterHeaders["x-provider-secret"]` is false. The classifier is
never called on the response path. This holds on the tested `dev` commit and on
the current release `transports/v2.1.1`, where the map is byte-identical.

## Gate 1: correctness

### Exact reproduction

```text
git clone https://github.com/maximhq/bifrost.git /tmp/bifrost-077
git -C /tmp/bifrost-077 checkout 44a562431ee0463cb1afe0e5833cde07d7921701
make -C /tmp/bifrost-077 setup-workspace
mkdir -p /tmp/bifrost-077/transports/bifrost-http/ui
touch /tmp/bifrost-077/transports/bifrost-http/ui/index.html
(cd /tmp/bifrost-077/transports/bifrost-http && \
  go build -ldflags='-X main.Version=1.8.6-44a562431e' \
  -o /tmp/bifrost-077-http .)

BIFROST_SOURCE=/tmp/bifrost-077 \
BIFROST_BIN=/tmp/bifrost-077-http \
python3 transcripts/077/reproduce.py
```

The zero-byte `ui/index.html` satisfies the official binary's embed input. It
does not replace any HTTP, provider, authentication, configuration, or inference
code used by the reproduction.

- Expected behavior: Provider credentials must not be returned to inference callers. The existing upstream regression test says provider auth headers are "never forwarded to clients" while benign headers pass.
- Observed behavior: Every authenticated response contains `CANARY_ADMIN_ONLY_PROVIDER_SECRET_077` as `X-Provider-Secret` and again in JSON metadata.
- Raw request evidence: [`observed.http`](../../transcripts/077/observed.http)
- Raw response evidence: [`observed.http`](../../transcripts/077/observed.http)
- Forwarded-request evidence, if applicable: [`upstream.http`](../../transcripts/077/upstream.http)
- Reproduction rate: 5/5 authenticated requests leaked in both client-visible locations; 5/5 unauthenticated controls returned 401; 5/5 management-boundary controls refused the same virtual key; both same-response controls passed 5/5.
- Smallest isolated trigger: An upstream response header whose name is sensitive under Bifrost's own `IsSensitiveHeader` classifier but absent from `providerResponseFilterHeaders`. `X-Provider-Secret` is the minimal tested name.

The runner verifies the pinned Git revision, core version, relevant source files,
Go binary VCS revision, runtime binary version, status codes, raw upstream receipt, and every trial before
writing any fixture. It starts the real Bifrost HTTP binary and calls its public
Responses route. The Bifrost child receives an allowlisted environment with no
provider credentials.

Only the fasthttp extractor is exercised at runtime. The other two share the same
map by direct indexing at `utils.go:992` and `utils.go:1017`, so the gap is
identical there by construction, but this pull request does not claim a runtime
reproduction through a passthrough route or a Bedrock-style provider.

`results.json` records `binary_vcs_modified: false` for a clean checkout at the
pinned revision. Independently of that flag, the runner asserts with
`git diff --quiet` that the five files implementing response header forwarding, filtering, and sensitive-name
classification are byte-identical to the pinned revision:
`core/providers/utils/utils.go`, `core/schemas/headers.go`, `core/schemas/provider.go`,
`core/schemas/trace.go`, and `transports/bifrost-http/handlers/inference.go`. The
run aborts if any of them differ, so the observed leak comes from unmodified
upstream code.

The runner asserts the presence of the leak, so it exits non-zero against a
patched Bifrost with "secret missing from HTTP response header". That inverted
failure is the intended acceptance signal for a fix, not a harness fault.

### Control

```text
# The reproduction sends these three response headers in the same upstream response:
X-Provider-Secret: CANARY_ADMIN_ONLY_PROVIDER_SECRET_077
Authorization: Bearer CANARY_AUTHORIZATION_CONTROL_077
X-Safe-Trace: CANARY_SAFE_TRACE_077

# It also repeats the identical public request without x-bf-vk,
# and presents the same virtual key to the management API.
BIFROST_SOURCE=/tmp/bifrost-077 \
BIFROST_BIN=/tmp/bifrost-077-http \
python3 transcripts/077/reproduce.py
```

- Control result: Bifrost strips `Authorization` from the HTTP header and JSON metadata 5/5, preserves `X-Safe-Trace` in both places 5/5, rejects the same inference request without the virtual key 5/5, and refuses the virtual key at `GET /api/providers` 5/5.
- Why this attributes the failure to the claimed layer: One valid upstream response simultaneously exercises the fixed credential denylist, the missing generic classifier, and benign forwarding. Only `X-Provider-Secret` crosses the authenticated boundary. Name-for-name, `Authorization` and `X-Provider-Secret` are both credentials under `IsSensitiveHeader`; only the one that also appears in the fixed map is stripped. [`unauthenticated-control.http`](../../transcripts/077/unauthenticated-control.http) proves the route enforces inference authentication, and [`management-boundary-control.http`](../../transcripts/077/management-boundary-control.http) proves the leaking caller cannot read provider configuration through the supported path.

### False-positive checks

- [x] Tested the exact cited behavior, not a similar symptom.
- [x] Pinned and reported the target version or commit.
- [x] Ruled out bad configuration and malformed input.
- [x] Ruled out model nondeterminism or reported why it is irrelevant.
- [x] Confirmed the failure is not created only by the mock or harness.
- [x] Sanitized all recorded evidence.

The local upstream returns valid HTTP and a valid OpenAI Responses body. Its only
job is to reflect the configured static header. The claimed defect is precisely
that Bifrost fails to filter that valid provider response before crossing a
caller boundary. No model generation or provider-specific behavior is claimed.

Reflection is not an artifact of this harness. Upstream's own regression test
comment names it as the real-world trigger it was written for: provider auth
headers "Bifrost injects upstream (and some upstreams echo back, e.g. Google's
file-download 302)". The capture upstream reproduces exactly that shape, and the
forwarded-request evidence in [`upstream.http`](../../transcripts/077/upstream.http)
shows the value originating from Bifrost's own configured request header.

[`expected.http`](../../transcripts/077/expected.http) is not a recording. It is
the observed exchange with the secret removed from both client-visible locations,
written by the runner so the conformance suite has a passing counterpart fixture.

## Gate 2: usefulness

### Who gets bitten

- Affected user or customer: An operator running Bifrost as an authenticated shared gateway with provider authentication or a proxy credential in static extra headers.
- Real workflow: A workspace administrator configures a custom OpenAI-compatible provider and gives an application a virtual key scoped to that provider. The application makes a normal Responses request.
- Preconditions and likely frequency: The configured header name matches Bifrost's generic sensitive-header rules but not its short response denylist, and the provider or an intermediate proxy reflects that header. Under those conditions the disclosure occurred on every response, 5/5. How often production upstreams reflect custom headers was not measured, and it is the narrowest link in the chain. Major first-party providers do not routinely echo request headers; self-hosted OpenAI-compatible servers, chained gateways, and reverse proxies with header passthrough are the realistic population.

### Observable consequence

- User action: A scoped application calls `POST /v1/responses` with its virtual key.
- Wire-level defect: Bifrost copies the reflected administrator-only provider credential into an HTTP response header and JSON response metadata.
- End-user or agent-level failure: The scoped caller obtains a provider credential it was never given, allowing it to bypass Bifrost controls wherever that credential is accepted.
- Consumer-boundary demonstration or transcript: [`observed.http`](../../transcripts/077/observed.http) is the exact authenticated client exchange. The runner's consumer assertion extracts the administrator canary 5/5. [`unauthenticated-control.http`](../../transcripts/077/unauthenticated-control.http) shows the same route is protected without a virtual key, and [`management-boundary-control.http`](../../transcripts/077/management-boundary-control.http) shows the same virtual key refused by the management API.
- Measured impact: Disclosure of the exact synthetic provider-secret value to a provider-scoped virtual-key caller in two client-visible locations, 5/5.
- Inferred impact, clearly labeled: A real reflected authentication value could be reused against its upstream provider or proxy, bypassing Bifrost budgets, rate limits, and audit controls. No real credential was used or replayed.

### Bug or not

- Expected behavior confirmed as intended spec, not a stale doc line (cite examples, tests, or UI): Yes. Upstream's [`TestExtractProviderResponseHeaders_StripsProviderSecrets`](https://github.com/maximhq/bifrost/blob/44a562431ee0463cb1afe0e5833cde07d7921701/core/providers/utils/utils_test.go#L1961-L1993) states that provider auth headers are never forwarded to clients while benign headers pass. The [provider UI](https://github.com/maximhq/bifrost/blob/44a562431ee0463cb1afe0e5833cde07d7921701/ui/app/workspace/providers/fragments/networkFormFragment.tsx#L522-L542), [configuration docs](https://github.com/maximhq/bifrost/blob/44a562431ee0463cb1afe0e5833cde07d7921701/docs/quickstart/gateway/provider-configuration.mdx), and [original extra-header PR #85](https://github.com/maximhq/bifrost/pull/85) establish that static extra headers and custom authentication headers are supported configuration.
- Maintainer ruling searched (commit, PR, comment, denylist) and result: No maintainer ruling allows custom credential headers through responses. [#3954](https://github.com/maximhq/bifrost/issues/3954) classified the same boundary crossing for `x-goog-api-key` as a security bug, [#3955](https://github.com/maximhq/bifrost/pull/3955) attempted to strip provider secrets by adding three exact names, and [#6371](https://github.com/maximhq/bifrost/pull/6371) expanded the generic classifier for identity-aware-proxy names without connecting it to this path. Open issue [#5509](https://github.com/maximhq/bifrost/issues/5509) states the maintainer-facing position directly: "Provider response headers may contain credentials or other sensitive information."
- Trigger is supported usage with default or recommended settings: Yes. `network_config.extra_headers` is present in the UI, API schema, configuration docs, and custom-provider flow. PR #85 explicitly names custom authentication headers as a use case. Response-header forwarding is on by default with no opt-in flag.
- Boundary crossed (role or key scope that must not see the data, with auth enabled): The virtual key is an inference-only credential by upstream's explicit design. `AuthMiddleware.APIMiddleware` is constructed with `allowVirtualKeyAuth=false`, and the comment on `InferenceMiddleware` at [`middlewares.go:1080`](https://github.com/maximhq/bifrost/blob/44a562431ee0463cb1afe0e5833cde07d7921701/transports/bifrost-http/handlers/middlewares.go#L1080-L1090) states that admin-password auth "stays exclusive to dashboard/API routes (APIMiddleware)" precisely "since a VK is not an admin/session credential". This run therefore enables `governance.auth_config` rather than leaving the management API open, and records the same virtual key being refused at `GET /api/providers` 5/5 while it succeeds at `POST /v1/responses` 5/5. The credential the caller cannot read through the management API arrives in the inference response anyway.
- Fix a maintainer would ship, in one sentence: Drop every header matched by `schemas.IsSensitiveHeader` in all three provider-response extractors, in addition to the existing transport denylist, while preserving benign provider headers and the passthrough `content-type` carve-out.
- Label: `bug`

## Gate 3: upstream status

- Date checked: 2026-09-13
- Upstream version checked: current `dev` commit `44a562431ee0463cb1afe0e5833cde07d7921701`; latest transport release [v2.1.1](https://github.com/maximhq/bifrost/releases/tag/transports/v2.1.1) at `c193745d2a713e9f58f021d43e138df5eb7e038a` was inspected statically and has a byte-identical `providerResponseFilterHeaders`
- Search terms used: `"provider response headers" secret`, `"provider response headers" credential`, `"response header" leak`, `"X-Provider-Secret"`, `IsSensitiveHeader`, `ExtractProviderResponseHeaders`, `providerResponseFilterHeaders`, `provider_response_headers`, and `extra_headers response secret`
- Issues searched: Open and closed Bifrost issues using each exact query and variants for `custom header`, `auth header`, `sensitive header`, `echo`, and `metadata`
- Pull requests searched: Open, closed, and merged Bifrost pull requests with the same queries and the function names
- Releases, changelog, and documentation searched: transport v2.1.1, all current changelog entries mentioning provider response headers, provider configuration docs, deployment provider docs, adding-a-provider docs, UI provider form, schemas, examples, and clients
- Relevant commits searched: `git log -S` for `providerResponseFilterHeaders`, `x-goog-api-key`, `IsSensitiveHeader`, and all three extraction function names; compared the relevant filter and handler paths between v2.1.1 and the tested `dev` commit
- Matching links: [security issue #3954](https://github.com/maximhq/bifrost/issues/3954), [incomplete fix #3955](https://github.com/maximhq/bifrost/pull/3955), [generic sensitive-header work #6371](https://github.com/maximhq/bifrost/pull/6371), [provider-response observability discussion #5509](https://github.com/maximhq/bifrost/issues/5509), [feature origin #1666](https://github.com/maximhq/bifrost/pull/1666), [extra-header support #85](https://github.com/maximhq/bifrost/pull/85), [issues query](https://github.com/maximhq/bifrost/issues?q=is%3Aissue+%22provider+response+headers%22+secret), [pull-request query](https://github.com/maximhq/bifrost/pulls?q=is%3Apr+ExtractProviderResponseHeaders+sensitive)

Classification:

- [ ] Novel
- [ ] Duplicate, open and still reproducible
- [ ] Fixed on current release
- [ ] Regression
- [ ] Documented behavior
- [x] Discussed upstream without a dedicated ticket
- [ ] Incomplete, current upstream state could not be verified

The prior report and fix cover four exact authentication header names. This
finding demonstrates that the same client boundary still fails for supported,
administrator-configured credential header names that Bifrost's own generic
classifier recognizes. No dedicated ticket or open fix for that gap was found.
Current issue #5509 independently warns that complete provider response-header
maps may contain credentials, but it proposes safe observability rather than
repairing this client response path.

Runtime reproduction targets current `dev`. The latest v2.1.1 release has the
same fixed denylist and generic classifier gap by source comparison, but this
pull request does not claim a second runtime reproduction on that tag.

## Frozen invariant

- Issue writeup: `issues/077-bifrost-custom-response-header-secret-leak/README.md`
- Checker added or updated: Existing `response_omits_secret`, unchanged
- Conformance test added or updated: Issue 077 tests in `crates/harness/tests/conformance.rs`
- Why the checker tests the invariant rather than one implementation detail: It scans the full client-visible exchange for a caller-selected deployment-secret canary. The check catches disclosure in any header, metadata field, or body location without depending on Bifrost field names.

## Validation

| Check | Command | Result |
|---|---|---|
| Reproduction | `BIFROST_SOURCE=/tmp/bifrost-077 BIFROST_BIN=/tmp/bifrost-077-http python3 transcripts/077/reproduce.py` | PASS, 5/5 leak and all controls 5/5 |
| Control | Same command, `Authorization`, safe-header, unauthenticated, and management-boundary cells | PASS, 5/5 each |
| Harness | `cargo test --workspace` | PASS |
| Formatting | `cargo fmt --all -- --check` | PASS |
| Lint | `cargo clippy --workspace --all-targets -- -D warnings` | PASS |
| README counts | `python3 tools/update-readme-counts.py --check` | PASS |

## Security and scope

- [x] No API key, credential, private prompt, or unsanitized response is committed.
- [x] Only environment variable names appear in commands and documentation.
- [x] The pull request contains one finding.
- [x] Unrelated generated files and local state are excluded.

All committed values are conspicuously named synthetic canaries. Captured virtual
key and provider authorization values are replaced with placeholders.

## Author verdict

- Correctness: PASS
- Usefulness: PASS
- Upstream status: PASS
- Overall: ACCEPT

## Independent review

Run `.github/agents/kairo-reproduction-reviewer.agent.md` against this pull
request. Approval is blocked until the reviewer independently reruns the critical
path and all three gates pass.
