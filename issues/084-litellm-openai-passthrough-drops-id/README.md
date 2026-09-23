# 084, LiteLLM OpenAI pass-through strips standalone search request ID

- **Upstream**: duplicate of [litellm#42656](https://github.com/BerriAI/litellm/issues/42656), open; fix PR [litellm#42661](https://github.com/BerriAI/litellm/pull/42661), open on 2026-09-23.
- **Tool under test**: LiteLLM Proxy 1.102.1 from PyPI. Matching release tag commit: `d09bbae1c6df463e425558f60d460437193635da`. Python 3.12.13.
- **Reproduced**: 2026-09-23 UTC, real LiteLLM CLI, no model invocation. Client request dialect: OpenAI pass-through; route `POST /openai_passthrough/v1/alpha/search`; backend: deterministic local HTTP capture.
- **Model field**: `gpt-5.6-luna`, carried as request data only. No provider model ran.
- **Configuration**: `OPENAI_API_BASE=http://127.0.0.1:9996`; `OPENAI_API_KEY` set to a synthetic value, not recorded. Proxy auth was disabled for this local forwarding-only reproduction.

## What breaks

LiteLLM Proxy 1.102.1 removes the top-level `id` from OpenAI pass-through JSON requests. A Codex user calling standalone web search through `/openai_passthrough/v1/alpha/search` reaches the provider without the required ID and receives HTTP 400 instead of search results.

## Gate 1: correctness

### Exact reproduction

Installed the pinned public package with the proxy extra in a clean Python 3.12.13 environment, then started the real `litellm` CLI on `127.0.0.1:4010`. The upstream capture listened on `127.0.0.1:9996`. No dependency or application code in this repository was changed.

```sh
uv venv /tmp/litellm-1.102.1 --python 3.12
uv pip install --python /tmp/litellm-1.102.1/bin/python 'litellm[proxy]==1.102.1'
python3 transcripts/084/capture_upstream.py
# In another terminal, set OPENAI_API_KEY to a synthetic value.
OPENAI_API_BASE=http://127.0.0.1:9996 /tmp/litellm-1.102.1/bin/litellm --host 127.0.0.1 --port 4010
# In a third terminal:
/tmp/litellm-1.102.1/bin/python transcripts/084/replay.py
```

The replay makes five direct calls with the exact client JSON and five calls through LiteLLM. The deterministic upstream returns HTTP 200 when `id` is present and the provider-style missing-ID HTTP 400 otherwise.

- **Expected behavior**: `/openai_passthrough` forwards this less-common OpenAI endpoint request with its provider-required fields intact. LiteLLM documents this route for endpoints needing guaranteed pass-through and for newer endpoints it does not fully support.
- **Observed behavior**: the client JSON has `id`; all five captured requests forwarded by LiteLLM omit only that key. All five proxied calls receive `400 missing_required_parameter`. The five direct controls include `id` at the capture and receive HTTP 200.
- **Raw client request**: `transcripts/084/raw/replay/client-request.json` and the exact HTTP request is `transcripts/084/raw/replay/client-proxy-01-request.http` (the other four are numbered 02 through 05).
- **Raw direct control responses**: `transcripts/084/raw/replay/client-direct-01-response.http` through `-05-response.http`.
- **Raw proxied client responses**: `transcripts/084/raw/replay/client-proxy-01-response.http` through `-05-response.http`.
- **Direct upstream request/response**: raw HTTP is in `transcripts/084/raw/replay/upstream-request-01.http` through `-05.http` and matching `upstream-response-*.http`.
- **Forwarded upstream request/response**: raw HTTP forwarded by LiteLLM is in `transcripts/084/raw/replay/upstream-request-06.http` through `-10.http` and matching `upstream-response-*.http`. The synthetic upstream `api-key` header is redacted inline.
- **Reproduction rate**: 5/5 direct controls succeed; 5/5 proxied requests lose `id` and fail.
- **Smallest trigger**: sending the same JSON through the OpenAI pass-through route causes LiteLLM to remove top-level `id`; direct forwarding of that same JSON preserves it. The affected workflow requires an OpenAI standalone search call with an `id` sent through this route. Its production frequency was not measured.

The provider was not called directly because no OpenAI credential was available in the environment. This claim is about LiteLLM's forwarded bytes, so a local deterministic capture is the appropriate attribution control. The upstream reporter records the actual OpenAI 400 and the Codex search workflow in issue #42656. The mock does not cause the gateway's byte loss; captured bytes from the real pinned LiteLLM process show the ID missing before the mock evaluates the request.

### Root cause

In the pinned 1.102.1 source, `id` is in `litellm/types/utils.py:3777` within `all_litellm_params`. `litellm/proxy/pass_through_endpoints/pass_through_endpoints.py:569` pops every listed field from `_parsed_body`. The pass-through handler therefore treats a provider request ID as LiteLLM-only metadata.

## Gate 2: usefulness

### Who gets bitten

A Codex user who enables live standalone web search while routing its OpenAI API calls through LiteLLM's documented `/openai_passthrough` route. LiteLLM's OpenAI pass-through docs say this route is for any endpoint that needs guaranteed pass-through and for newer OpenAI endpoints not fully supported by LiteLLM. The same request shape and Codex workflow are recorded in upstream issue #42656.

### Observable consequence

- **User action**: Codex performs a live web search through the LiteLLM OpenAI pass-through route.
- **Wire defect**: LiteLLM removes the request's `id` before forwarding it.
- **Consumer failure**: OpenAI returns `400 missing_required_parameter`; the search operation fails instead of returning results.
- **Consumer-boundary demonstration**: the real proxy returns HTTP 400 to the caller in 5/5 local capture trials; the direct control with the same body receives HTTP 200 from the deterministic endpoint in 5/5. The upstream reporter records the actual provider 400, LiteLLM access log, and Codex workflow in issue #42656. The provider call was not independently repeated here.
- **Measured impact**: failure is deterministic for the tested request shape, 5/5. Production frequency was not measured.
- **Inferred impact**: Codex tasks requiring web results cannot use this route until the request ID is preserved.

### Bug or not

- **Expected behavior is the intended contract**: LiteLLM's [OpenAI pass-through docs](https://docs.litellm.ai/docs/pass_through/openai_passthrough) describe direct OpenAI API access, guaranteed pass-through for any endpoint, and use for newer endpoints LiteLLM does not fully support. The open fix PR adds a regression test that expects the provider-owned `id` to remain in the forwarded body.
- **Maintainer ruling**: searched the pinned source and current issue and PR. No intentional rule was found that classifies provider request IDs as LiteLLM-only. The open fix PR explicitly preserves `id`; no contrary ruling was found.
- **Supported usage**: the `/openai_passthrough` route is documented for newer or unsupported OpenAI endpoints.
- **Boundary crossed**: not a disclosure claim.
- **Maintainer fix**: exclude provider-owned top-level `id` from LiteLLM-only pass-through fields removed from the request body.
- **Label**: `bug`.

## Gate 3: upstream status

- **Date checked**: 2026-09-23.
- **Upstream release checked**: LiteLLM v1.102.1, published 2026-09-23; the pinned tag still contains the `id` extraction.
- **Search terms**: `"/alpha/search"`, `"standalone search id"`, `"provider request ID in pass-through endpoints"`, `"all_litellm_params"`, `"OpenAI pass-through strips required id"`.
- **Issues and PRs searched**: exact endpoint, `id`, missing required parameter, standalone search, and pass-through request ID terms; matching [issue #42656](https://github.com/BerriAI/litellm/issues/42656) is open; [PR #42661](https://github.com/BerriAI/litellm/pull/42661) is open and proposes preserving provider request IDs.
- **Release notes, docs, and code searched**: current latest release v1.102.1 and its tag, the release entry, current OpenAI pass-through docs, pinned source, upstream issue, and fix PR diff. The v1.102.1 release does not include the open fix PR.
- **Classification**: duplicate-open, independently reproduced on current latest stable 1.102.1. This Kairo reproduction adds five real-proxy wire captures, a five-trial direct control, and an invariant checker over both.
- **Why this report adds value**: the upstream issue reports the real OpenAI 400 from a Codex workflow. Kairo independently runs LiteLLM 1.102.1 through its real CLI and retains the client, forwarded, and response bytes across five direct and five proxied trials.

## Frozen invariant

- **Invariant**: for a pass-through request, a provider-owned top-level `id` present in the client JSON remains present in the upstream JSON.
- **Checker**: `provider_request_id_preserved` in `crates/harness/src/checks.rs` verifies that a top-level ID present in the client JSON remains value equivalent in the forwarded JSON. Conformance tests cover the five failing proxy captures, five direct controls, and changed or absent IDs.

## Test

The replay and raw captures are under `transcripts/084/`. The conformance suite uses the same captured client and forwarded bodies for each trial. The checker tests the invariant itself, so it also detects a changed ID and permits requests that did not include one.

## Validation

| Check | Command | Result |
|---|---|---|
| Pinned package and real CLI route | See setup commands above | Passed, LiteLLM 1.102.1 |
| Direct capture control | `transcripts/084/replay.py` | Passed 5/5 |
| Proxied capture reproduction | `transcripts/084/replay.py` | Failed as claimed 5/5 |
| Harness | `cargo test --workspace` | Passed, 199 tests |
| Formatting | `cargo fmt --all -- --check` | Passed |
| Lint | `cargo clippy --workspace --all-targets -- -D warnings` | Passed |
| README counts | `python3 tools/update-readme-counts.py --check` | Passed, 61 findings and 199 tests |
| Independent reproduction review | `.github/agents/kairo-reproduction-reviewer.agent.md` | Passed; reviewer independently reran 5/5 proxy failures and 5/5 direct controls, then returned ACCEPT |

## Author verdict

- **Correctness**: PASS for LiteLLM's body mutation. The provider's real response is reported in upstream issue #42656; the provider was not independently called in this reproduction.
- **Usefulness**: PASS; the call returns an error instead of a usable search result.
- **Upstream status**: PASS, duplicate-open with a current open fix PR.
- **Overall**: ACCEPT. All three gates and repository checks passed, including independent reproduction review.

## Independent review

The read-only reviewer independently installed LiteLLM 1.102.1, reran the real proxy and direct capture controls at 5/5 each, confirmed the forwarded request loses only `id`, checked the current upstream issue and fix PR, and passed all required Kairo checks. The reviewer returned ACCEPT on 2026-09-23.
