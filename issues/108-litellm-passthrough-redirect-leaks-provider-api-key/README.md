# 108, LiteLLM pass-through follows a cross-origin redirect and leaks the provider `api-key`

## Claim

A LiteLLM `/openai` pass-through request follows a cross-origin 307 from the
configured provider base URL and forwards the provider credential in the
`api-key` header to the redirect target. `httpx` strips the same credential from
`authorization` on that hop, and the client still receives HTTP 200.

- Upstream project: [BerriAI/litellm](https://github.com/BerriAI/litellm)
- Cited upstream issue or report: none public. LiteLLM's
  [`SECURITY.md`](https://github.com/BerriAI/litellm/security/policy) requires a
  private GitHub vulnerability report with a reproduction video, so no public
  upstream issue has been filed. Nearest public reports are
  [#41295](https://github.com/BerriAI/litellm/issues/41295) (Azure pass-through
  model allowlist bypass) and
  [#41467](https://github.com/BerriAI/litellm/issues/41467) (Watsonx pass-through
  model allowlist bypass); both are different from a redirect credential leak
- Target type: open-source, run locally
- Tested release or commit: LiteLLM `v1.106.2`,
  `49943cd52ea46aacf8db2e5cb2b890d580fe9bb4` (latest release, 2026-10-21);
  the same code is on `main` at
  `f2118fc7670ba3fa736d9b8f4deef9077a45a720` (2026-10-22). `httpx` 0.28.1,
  Python 3.11.9, macOS arm64
- Client dialect and endpoint: OpenAI Chat Completions through the LiteLLM
  pass-through, `POST /openai/v1/chat/completions`
- Backend dialect and provider or capture upstream: deterministic loopback HTTP
  servers. The configured origin returns a 307 to a second origin; the second
  origin (the sink) returns a valid OpenAI chat completion. No provider is
  contacted
- Model, if relevant: `mock` from
  [`transcripts/108/config.yaml`](../../transcripts/108/config.yaml); the
  pass-through request itself is the trigger and no model output is part of the
  claim
- Relevant configuration: `master_key` only, no database,
  `OPENAI_API_BASE=http://127.0.0.1:<origin>`, and a synthetic canary in
  `OPENAI_API_KEY`. No security control is disabled

## Gate 1: correctness

### Exact reproduction

The runner starts the real LiteLLM proxy CLI twice, once with
`OPENAI_API_BASE` pointing at a 307 origin and once at a direct 200 origin. It
also starts the origin and sink capture servers, authenticates with the master
key, and sends five identical requests to each proxy. The request body is a
valid Chat Completions body for the configured `mock` model. The only
behavioral difference between the trigger and the control is the configured
origin's response status; the two local origins use different ephemeral ports by
construction.

```text
# from a clean checkout of this branch
uv venv --python 3.11 /tmp/kairo-108-venv
uv pip install --python /tmp/kairo-108-venv/bin/python 'litellm[proxy]==1.106.2'
/tmp/kairo-108-venv/bin/python transcripts/108/reproduce.py \
  --litellm-bin /tmp/kairo-108-venv/bin/litellm \
  --output-dir /tmp/kairo-108-review
```

The script prints one JSON object per run. It writes only sanitized evidence to
the output directory. The committed run is in
[`transcripts/108`](../../transcripts/108). The proxy logs may contain the
synthetic canaries, so they stay in the output directory and are not committed.

- Expected behavior: the provider credential reaches the configured origin. A
  cross-origin redirect target is not the configured origin and must not receive
  the credential. The pass-through should stop at the redirect or remove
  credential headers on the cross-origin hop.
- Observed behavior: the configured origin receives
  `authorization: Bearer [CANARY_PROVIDER_KEY]` and
  `api-key: [CANARY_PROVIDER_KEY]`. The sink receives
  `api-key: [CANARY_PROVIDER_KEY]` and no `authorization`. The client receives
  HTTP 200 with the sink's completion body.
- Raw request evidence:
  `transcripts/108/trigger/run-01/upstream-origin-01.http` (the request the
  proxy sent to the configured origin) and
  `transcripts/108/trigger/run-01/upstream-sink-01.http` (the request the proxy
  sent to the redirect target)
- Raw response evidence:
  `transcripts/108/trigger/run-01/client-response.http` (HTTP 200). The origin's
  307 response is not recorded as a response by the capture server; the sink
  request and the client response show the follow
- Forwarded-request evidence:
  `transcripts/108/trigger/run-01/sink.jsonl` and
  `transcripts/108/trigger/run-01/origin.jsonl` contain the same requests as
  machine-readable header maps. The raw `.http` files are the byte evidence
- Reproduction rate: trigger 5/5, sink `api-key` 5/5, sink `authorization` 0/5,
  client HTTP 200 5/5. Control 5/5, control sink requests 0/5, control client
  HTTP 200 5/5
- Smallest isolated trigger: the configured origin's response is 307 instead of
  200. The client request, proxy process, authentication, model, body, headers,
  and the fact that the configured origin is the only upstream stay identical.
  The two local origins use different ephemeral ports, but each is the only
  configured `OPENAI_API_BASE` in its cell. Removing the redirect removes the
  sink request and the credential never leaves the configured origin

The trigger and control each have five runs. The per-run result files are
`transcripts/108/trigger/run-01..05/result.json` and
`transcripts/108/control/run-01..05/result.json`.

### Wire evidence

The configured origin receives both credential headers:

```http
POST /v1/chat/completions HTTP/1.1
host: 127.0.0.1:<origin-port>
authorization: Bearer [CANARY_PROVIDER_KEY]
api-key: [CANARY_PROVIDER_KEY]
content-type: application/json

{"model":"mock","messages":[{"role":"user","content":"hello"}]}
```

The origin answers 307 with `Location: http://127.0.0.1:<sink-port>/sink`.
`httpx` follows that location. The sink receives the same secret in the
`api-key` header, but `authorization` is gone:

```http
POST /sink HTTP/1.1
host: 127.0.0.1:<sink-port>
user-agent: litellm/1.106.2
api-key: [CANARY_PROVIDER_KEY]
content-type: application/json

{"model":"mock","messages":[{"role":"user","content":"hello"}]}
```

The client receives HTTP 200 and the sink's completion. It cannot tell that the
credential crossed an origin boundary. The full sanitized bytes are under
[`transcripts/108`](../../transcripts/108).

### Control

The control proxy uses the same config, master key, request body, request
headers, model, auth path, and pass-through handler. `OPENAI_API_BASE` points at
a direct 200 origin instead of the 307 origin.

- Control result: the configured origin receives `authorization` and `api-key`
  5/5. The sink receives no request 5/5. The client receives HTTP 200 5/5. The
  control sink files are empty by design.
- Why this attributes the failure to the claimed layer: the only changed
  variable is the origin's 307. The credential is expected at the configured
  origin in both cells. Only the redirect adds a second origin, and only the
  redirect causes the `api-key` to reach it. The `authorization` header is
  stripped on the same cross-origin hop, which shows the client has a
  cross-origin credential rule that the `api-key` spelling bypasses.

### False-positive checks

- [x] Tested the exact cited behavior, not a similar symptom.
- [x] Pinned the target release or commit.
- [x] Ruled out bad configuration and malformed input.
- [x] Ruled out model nondeterminism or reported why it is irrelevant.
- [x] Confirmed the failure is not created only by the mock or harness.
- [x] Sanitized all recorded evidence.

The mock origin is appropriate because the claim is about what the gateway
forwards. LiteLLM itself performs authentication, pass-through routing, header
assembly, and the outbound HTTP request. The origin and sink only return
deterministic HTTP bytes. There is no model output in the claim. The 307 is a
valid upstream response, the body is valid for the configured model, the client
gets 200, and the master key is set. The same `httpx` client and the same
`BaseOpenAIPassThroughHandler` run in the trigger and the control.

## Root cause

LiteLLM assembles two headers from the same provider credential:

- [`llm_passthrough_endpoints.py` line 3417](https://github.com/BerriAI/litellm/blob/49943cd52ea46aacf8db2e5cb2b890d580fe9bb4/litellm/proxy/pass_through_endpoints/llm_passthrough_endpoints.py#L3417)
  sets `authorization: Bearer <key>` and `api-key: <key>`. The
  `openai_proxy_route` at line 3042 and
  `base_openai_pass_through_handler` at line 3348 use this assembly.
- [`http_handler.py` line 633](https://github.com/BerriAI/litellm/blob/49943cd52ea46aacf8db2e5cb2b890d580fe9bb4/litellm/llms/custom_httpx/http_handler.py#L633)
  defaults `follow_redirects` to `True`, and line 680 passes it to
  `httpx.AsyncClient`.
- [`pass_through_endpoints.py` line 500](https://github.com/BerriAI/litellm/blob/49943cd52ea46aacf8db2e5cb2b890d580fe9bb4/litellm/proxy/pass_through_endpoints/pass_through_endpoints.py#L500)
  sends the generic pass-through request with
  `async_client.send(generic_request, stream=True)` and does not override the
  client's redirect policy.

`httpx` 0.28.1 removes `authorization` on a cross-origin redirect but does not
remove `api-key`. The result is a credential leak in the second header even
though the first header is protected. The same handler and client are used by
the `/openai`, `/openai_passthrough`, `/azure`, and `/azure_ai` pass-through
routes. The exact reproduction is `/openai`; the Azure routes share the header
assembly and the outbound client but were not run separately.

The one-sentence fix: set `follow_redirects=False` on the pass-through client, or
strip `api-key` (and the other provider credential spellings) on a cross-origin
redirect.

## Gate 2: usefulness

### Who gets bitten

- Affected user or customer: a self-hosted LiteLLM operator who configures a
  provider pass-through with an operator-owned provider key and lets internal
  users call it. The tested route is the OpenAI pass-through; the same
  credential assembly is shared with the Azure pass-through.
- Real workflow: an operator keeps one provider key in the LiteLLM environment
  and exposes `/openai/v1/chat/completions` (or the Azure equivalent) to
  application users. The proxy attaches the operator's key to every pass-through
  request.
- Preconditions and likely frequency: the configured origin or an intermediary
  must return a 307 or 308 to a different origin. That can be an open redirect
  on the trusted first hop, a reverse proxy, or a compromised first hop. A
  caller who can reach the pass-through route can trigger the redirect when the
  provider exposes an open redirect. The frequency of provider redirects was not
  measured. The measured consequence is 5/5 whenever the condition holds.

### Observable consequence

- User action: an authenticated caller sends a valid pass-through request.
- Wire-level defect: LiteLLM follows the cross-origin redirect and sends the
  operator's provider key in `api-key` to the redirect target.
- End-user or agent-level failure: the redirect target obtains a provider key
  that the operator scoped to a different origin. The client receives HTTP 200
  and no error, so neither the caller nor the operator gets a signal.
- Consumer-boundary demonstration or transcript: the sink request in
  `transcripts/108/trigger/run-01/upstream-sink-01.http` contains
  `api-key: [CANARY_PROVIDER_KEY]`. The control sink has no request. The
  configured origin has both headers in both cells.
- Measured impact: one complete provider credential crossed to the
  unconfigured origin 5/5 with authentication enabled. The same value is the
  `authorization` Bearer on the origin hop, so the sink can use it as a Bearer
  token against OpenAI, or as `api-key` against Azure.
- Inferred impact, clearly labeled: provider-side budget use or access to
  provider resources allowed to the stolen key. This was not attempted because
  the reproduction uses synthetic canaries only.

### Bug or not

- Expected behavior confirmed as intended spec, not a stale doc line: LiteLLM's
  pass-through documentation says the request is forwarded to the configured
  target URL and that the OpenAI route uses `OPENAI_API_KEY`; it does not say a
  provider key may be forwarded to a different origin. `httpx` itself treats
  `authorization` as sensitive on a cross-origin redirect, and LiteLLM puts the
  same secret in `api-key`, which the client does not treat as sensitive. The
  `/health` path also omits the `api_key` field, so the product treats the
  value as a secret. The same invariant is already recorded in this repository
  for Switchyard 063.
- Maintainer ruling searched and result: no issue, pull request, commit,
  comment, or denylist was found that permits a pass-through to forward the
  provider key across origins. `follow_redirects=True` is a general HTTP client
  default used by all provider calls, not a pass-through credential policy. The
  only explicit redirect credential policy in the stack is `httpx` stripping
  `authorization` on a cross-origin hop.
- Trigger is supported usage with default or recommended settings: the
  pass-through route is documented, the master key is set, the model is
  configured, and no security control is disabled. The 307 is a valid upstream
  response; the operator cannot prevent a provider or intermediary from
  redirecting. The open-redirect or compromised-first-hop condition is the same
  threat model used for the accepted 063 finding.
- Boundary crossed: the operator's provider key is scoped to the configured
  origin. The sink is a different origin, and it receives the key with
  authentication enabled. The caller does not need to read the key; the redirect
  target does.
- Fix a maintainer would ship, in one sentence: set `follow_redirects=False` on
  the pass-through client, or strip `api-key` and the other provider credential
  headers on a cross-origin redirect.
- Label: `bug`

## Gate 3: upstream status

- Date checked: 2026-10-22
- Upstream release and commit checked: `v1.106.2`
  [`49943cd5`](https://github.com/BerriAI/litellm/releases/tag/v1.106.2)
  (2026-10-21) and `main`
  [`f2118fc7`](https://github.com/BerriAI/litellm/commit/f2118fc7670ba3fa736d9b8f4deef9077a45a720)
  (2026-10-22). The `follow_redirects=True` default and the `api-key` assembly
  are unchanged on `main`
- Search terms used: `follow_redirects`, `cross-origin redirect`,
  `redirect credential`, `redirect api key`, `api-key redirect`,
  `pass-through redirect`, `pass-through credential`, `pass-through api key`,
  `follow redirects`, `redirect authorization header provider key`,
  `httpx api-key redirect`
- Issues searched: open and closed GitHub issues, including
  [#41295](https://github.com/BerriAI/litellm/issues/41295),
  [#41467](https://github.com/BerriAI/litellm/issues/41467),
  [#30079](https://github.com/BerriAI/litellm/issues/30079),
  [#40490](https://github.com/BerriAI/litellm/issues/40490),
  [#29190](https://github.com/BerriAI/litellm/issues/29190), and
  [#5763](https://github.com/BerriAI/litellm/issues/5763)
- Pull requests searched: open, closed, merged, and draft pull requests,
  including [#42033](https://github.com/BerriAI/litellm/pull/42033),
  [#26849](https://github.com/BerriAI/litellm/pull/26849), and
  [#30699](https://github.com/BerriAI/litellm/pull/30699)
- Releases, changelog, and documentation searched:
  [`v1.106.2` release](https://github.com/BerriAI/litellm/releases/tag/v1.106.2),
  the current releases list, the pass-through documentation, the OpenAI
  pass-through documentation, and the
  [security policy](https://github.com/BerriAI/litellm/security/policy)
- Relevant commits searched: the `pass_through_endpoints.py` history through
  2026-10-21 and the `http_handler.py` history through 2026-10-15. No commit
  changes the pass-through redirect policy or marks `api-key` as a sensitive
  cross-origin header
- Matching links: none for this behavior. The nearest items are the two
  pass-through model allowlist issues above, which concern model resolution and
  not credential forwarding. The 063 finding in this repository records the
  same class on Switchyard, not on LiteLLM
- Classification: Novel on public sources. LiteLLM's security policy requires a
  private report with a video, and no private report has been submitted yet.
  The kairo pull request is the reproduction, not the upstream report.

## Frozen invariant

- Issue writeup: this file.
- Checker added or updated: `upstream_omits_header_value` in
  `crates/harness/src/checks.rs` already scans every forwarded header for a
  secret marker. No new checker is needed; the existing checker is applied to the
  new captures.
- Conformance tests added in `crates/harness/tests/conformance.rs`:
  - `litellm_passthrough_redirect_sink_keeps_provider_api_key`
  - `litellm_passthrough_direct_origin_receives_key_but_sink_does_not`
  - `litellm_passthrough_redirect_leak_5_of_5`
- Why the checker tests the invariant rather than one implementation detail: it
  scans the complete forwarded request headers for the canary value. It does not
  depend on LiteLLM's internal class names, a specific HTTP library, or a
  specific redirect implementation. The control test asserts that the
  configured origin receives the key and that the unconfigured sink has no
  request. The 5/5 test asserts the reproduction rate.

## Validation

| Check | Command | Result |
|---|---|---|
| Reproduction | `/tmp/kairo-108-venv/bin/python transcripts/108/reproduce.py --litellm-bin /tmp/kairo-108-venv/bin/litellm --output-dir /tmp/kairo-108-review` | trigger 5/5, sink `api-key` 5/5; control 5/5, control sink 0/5 |
| Control | same command, built into the runner | configured origin gets both headers 5/5; control sink empty 5/5; client 200 10/10 |
| Harness | `cargo test --workspace` | PASS |
| Formatting | `cargo fmt --all -- --check` | PASS |
| Lint | `cargo clippy --workspace --all-targets -- -D warnings` | PASS |
| README counts | `python3 tools/update-readme-counts.py --check` | PASS, 79 folders and 320 Rust tests |
| Independent review | `.github/agents/kairo-reproduction-reviewer.agent.md` | PASS, ACCEPT (2026-10-22) |

## Security and scope

- [x] No API key, credential, private prompt, or unsanitized response is
      committed.
- [x] Only environment variable names appear in commands and documentation.
- [x] The pull request contains one finding.
- [x] Unrelated generated files and local state are excluded.

The runner uses fixed synthetic canaries and replaces them with
`[CANARY_PROVIDER_KEY]`, `[CANARY_MASTER_KEY]`, and `[CANARY_SALT_KEY]` before
writing any evidence file. The proxy logs are written to the output directory
and are not committed because they can contain the canaries. The `config.yaml`
contains only the unused `sk-mock` value for the configured model. No live
provider credential is read or written.

LiteLLM's security policy requires a private GitHub vulnerability report and a
video or terminal recording. A sanitized terminal recording can be prepared for
that report. No public upstream issue should be filed for this finding.

## Author verdict

- Correctness: PASS
- Usefulness: PASS
- Upstream status: PASS
- Overall: ACCEPT

## Independent review

Reviewed on 2026-10-22 by the kairo reproduction reviewer against `origin/main`
`6229551`. Verdict: `ACCEPT`.

The reviewer independently reran `transcripts/108/reproduce.py` (trigger 5/5,
control 5/5), the three focused conformance tests, `cargo test --workspace`,
`cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D
warnings`, and the README counts check. It inspected the raw trigger and control
bytes and searched the current upstream state. It confirmed the sink `api-key`
leak, the `authorization` strip, the empty control sink, the silent HTTP 200, and
the `novel` public-source classification. It found no blocking issues.

Non-blocking notes addressed here: the `/health` field is omitted rather than
masked (wording corrected), the origin 307 response is not separately recorded
(disclosed in Gate 1), and the control 5/5 loop is now frozen in the conformance
suite. The reviewer's captures are outside the repository.
