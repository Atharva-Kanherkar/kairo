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
rm -rf /tmp/kairo-084-rerun  # reviewer captures stay outside the repository
python3 transcripts/084/capture_upstream.py --output-dir /tmp/kairo-084-rerun
# In another terminal, set OPENAI_API_KEY to a synthetic value.
OPENAI_API_BASE=http://127.0.0.1:9996 /tmp/litellm-1.102.1/bin/litellm --host 127.0.0.1 --port 4010
# In a third terminal:
/tmp/litellm-1.102.1/bin/python transcripts/084/replay.py --output-dir /tmp/kairo-084-rerun
git status --porcelain  # prints nothing: the committed snapshot is unchanged
```

The replay makes five direct calls with the exact client JSON and five calls through LiteLLM. The deterministic upstream returns HTTP 200 when `id` is present and the provider-style missing-ID HTTP 400 otherwise.

The files under `transcripts/084/raw/replay/` are the frozen 2026-09-23 capture. Both scripts require `--output-dir`, so a rerun never writes there unless a maintainer passes that directory to both scripts to refresh the snapshot. Compare a rerun with `diff -r transcripts/084/raw/replay /tmp/kairo-084-rerun`. Proxied responses carry a new `date` and `x-litellm-call-id` on every run, and the redacted forwarded credential header can differ in name case between environments (`Authorization` in the snapshot, `authorization` in the 2026-09-23 review rerun). Every forwarded body still differs from the client body only by the missing `id`.

- **Expected behavior**: `/openai_passthrough` forwards this less-common OpenAI endpoint request with its provider-required fields intact. LiteLLM documents this route for endpoints needing guaranteed pass-through and for newer endpoints it does not fully support.
- **Observed behavior**: the client JSON has `id`; all five captured requests forwarded by LiteLLM omit only that key. All five proxied calls receive `400 missing_required_parameter`. The five direct controls include `id` at the capture and receive HTTP 200.
- **Raw client request**: `transcripts/084/raw/replay/client-request.json` and the exact HTTP request is `transcripts/084/raw/replay/client-proxy-01-request.http` (the other four are numbered 02 through 05).
- **Raw direct control responses**: `transcripts/084/raw/replay/client-direct-01-response.http` through `-05-response.http`.
- **Raw proxied client responses**: `transcripts/084/raw/replay/client-proxy-01-response.http` through `-05-response.http`.
- **Direct upstream request/response**: raw HTTP is in `transcripts/084/raw/replay/upstream-request-01.http` through `-05.http` and matching `upstream-response-*.http`.
- **Forwarded upstream request/response**: raw HTTP forwarded by LiteLLM is in `transcripts/084/raw/replay/upstream-request-06.http` through `-10.http` and matching `upstream-response-*.http`. The synthetic upstream `api-key` header is redacted inline.
- **Reproduction rate**: 5/5 direct controls succeed; 5/5 proxied requests lose `id` and fail.
- **Smallest trigger**: sending the same JSON through the OpenAI pass-through route causes LiteLLM to remove top-level `id`; direct forwarding of that same JSON preserves it. The affected workflow requires an OpenAI standalone search call with an `id` sent through this route. Its production frequency was not measured.

The deterministic capture isolates LiteLLM's forwarded bytes. On 2026-09-24 the live provider was also called with the `OPENAI_API_KEY` from the gitignored repository `.env`. Direct calls to OpenAI `/v1/alpha/search` returned HTTP 200 in 3/3 calls with `id` and HTTP 400 `missing_required_parameter` in 3/3 calls without it. The same request through LiteLLM 1.102.1 to OpenAI returned that 400 in 5/5 calls, while `/openai_passthrough/v1/models` on the same proxy and key returned HTTP 200. The real Codex CLI run below repeats this at the consumer boundary.

### Root cause

In the pinned 1.102.1 source, `id` is in `litellm/types/utils.py:3777` within `all_litellm_params`. `litellm/proxy/pass_through_endpoints/pass_through_endpoints.py:569` pops every listed field from `_parsed_body`. The pass-through handler therefore treats a provider request ID as LiteLLM-only metadata.

## Gate 2: usefulness

### Who gets bitten

A Codex user who enables live standalone web search while routing its OpenAI API calls through LiteLLM's documented `/openai_passthrough` route. LiteLLM's OpenAI pass-through docs say this route is for any endpoint that needs guaranteed pass-through and for newer OpenAI endpoints not fully supported by LiteLLM. The same request shape and Codex workflow are recorded in upstream issue #42656.

### Observable consequence

- **User action**: Codex performs a live web search through the LiteLLM OpenAI pass-through route.
- **Wire defect**: LiteLLM removes the request's `id` before forwarding it.
- **Consumer failure**: OpenAI returns `400 missing_required_parameter`; the search operation fails instead of returning results.
- **Consumer-boundary demonstration**: the real Codex CLI 0.156.1 was run through LiteLLM 1.102.1 to the live OpenAI API on 2026-09-24. Each setup ran once with default Codex config and once with `web_search="live"`. Evidence and scripts are in `transcripts/084/codex/`.

| Codex setup | Standalone search | Search calls | Forwarded with `id` | Result |
|---|---|---|---|---|
| Control: built-in provider, no gateway | on by default | 2 | 2 | HTTP 200; Codex answers with the release tag, 2/2 runs |
| A: built-in provider, `openai_base_url` = LiteLLM `/openai_passthrough/v1` | on by default | 11 | 0 | HTTP 400 missing `id`; Codex reports search failed, 2/2 runs |
| D: custom provider, `supports_standalone_web_search = true` (reporter's setup) | opted in | 13 | 0 | HTTP 400 missing `id`; Codex reports search failed, 2/2 runs |
| C: custom provider, flag unset | off by default | 0 | n/a | Codex offers no web search tool, so this defect is not reached |
| B: built-in provider, `openai_base_url` = LiteLLM `/v1` | on by default | 8 | n/a | LiteLLM returns HTTP 404 for `/v1/alpha/search`; a separate behavior not claimed here |

- **Why setup A matters**: Codex enables standalone search by default for its built-in `openai` provider (`supports_standalone_web_search: true` in `codex-rs/model-provider-info/src/lib.rs`). A custom provider defaults to `false`. So pointing the built-in provider at LiteLLM's pass-through route breaks web search with no opt-in.
- **Measured impact**: every one of the 24 Codex search calls through `/openai_passthrough` lost `id` and failed, and every affected Codex run ended without search results. The no-gateway control succeeded in 2/2 runs. Production frequency and the share of Codex users on setup A were not measured.
- **Inferred impact**: Codex users who route the built-in provider through LiteLLM's OpenAI pass-through lose web search entirely until the request ID is preserved. The failure is loud, not silent: Codex tells the user search failed and refuses to guess.

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

The replay and raw captures are under `transcripts/084/`. The Codex consumer-boundary captures are under `transcripts/084/codex/runs/`, with `codex_consumer_search_calls_lose_provider_request_id` covering every captured Codex search pair. The conformance suite uses the same captured client and forwarded bodies for each trial. The checker tests the invariant itself, so it also detects a changed ID and permits requests that did not include one.

## Validation

| Check | Command | Result |
|---|---|---|
| Pinned package and real CLI route | See setup commands above | Passed, LiteLLM 1.102.1 |
| Direct capture control | `transcripts/084/replay.py` | Passed 5/5 |
| Proxied capture reproduction | `transcripts/084/replay.py` | Failed as claimed 5/5 |
| Rerun leaves committed evidence intact | Documented commands, then `git status --porcelain` | Passed, no tracked file changed; both scripts exit 2 without `--output-dir` |
| Harness | `cargo test --workspace` | Passed, 200 tests |
| Formatting | `cargo fmt --all -- --check` | Passed |
| Lint | `cargo clippy --workspace --all-targets -- -D warnings` | Passed |
| README counts | `python3 tools/update-readme-counts.py --check` | Passed, 61 findings and 200 tests |
| Codex consumer boundary | `transcripts/084/codex/run_matrix.sh` against live OpenAI | 24/24 search calls through pass-through lost `id`; control 2/2 succeeded |
| Codex capture invariant | `codex_consumer_search_calls_lose_provider_request_id` | Passed, 24 violations detected |
| Independent reproduction review | `.github/agents/kairo-reproduction-reviewer.agent.md` | Passed; reviewer independently reran 5/5 proxy failures and 5/5 direct controls, then returned ACCEPT |

## Author verdict

- **Correctness**: PASS for LiteLLM's body mutation, confirmed against both the deterministic capture and the live OpenAI API.
- **Usefulness**: PASS; the real Codex CLI loses web search with default settings when its built-in provider points at LiteLLM's pass-through route.
- **Upstream status**: PASS, duplicate-open with a current open fix PR.
- **Overall**: ACCEPT. All three gates and repository checks passed, including independent reproduction review.

## Independent review

The read-only reviewer independently installed LiteLLM 1.102.1, reran the real proxy and direct capture controls at 5/5 each, confirmed the forwarded request loses only `id`, checked the current upstream issue and fix PR, and passed all required Kairo checks. The reviewer returned ACCEPT on 2026-09-23.
