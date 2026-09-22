# Finding 081: OGX silently drops adaptive thinking in translation mode

## Claim

OGX v1.4.0 accepts Anthropic `thinking: {"type":"adaptive"}` on
`POST /v1/messages`, returns HTTP 200, and forwards no thinking or reasoning
configuration to an OpenAI-compatible upstream.

- Upstream project: [ogx-ai/ogx](https://github.com/ogx-ai/ogx)
- Cited upstream report: merged [PR
  5938](https://github.com/ogx-ai/ogx/pull/5938) defines fail-closed behavior
  for `thinking.type == "enabled"` in translation mode, but does not cover
  `adaptive`
- Target type: open-source/local
- Tested release and commit: OGX v1.4.0, commit `051a8a0`
- Client dialect and endpoint: Anthropic Messages, `POST /v1/messages`
- Backend dialect: OpenAI Chat Completions, deterministic capture upstream
- Model: `openai/mock-gpt` in the deterministic reproduction
- Relevant configuration: `OPENAI_BASE_URL` points to the local capture
  upstream; `OPENAI_API_KEY` contains a synthetic test value; OGX runs with
  `--insecure --no-auth`

## Gate 1: correctness

### Exact reproduction

```sh
# OGX_SRC is any empty directory. KAIRO is this repository checkout.
OGX_SRC=/tmp/ogx-v1.4.0
KAIRO=$(pwd)

git clone --branch v1.4.0 https://github.com/ogx-ai/ogx "$OGX_SRC"
cd "$OGX_SRC" && uv sync
python3 "$KAIRO/transcripts/081/hunt.py" "$OGX_SRC"
```

The self-contained runner verifies commit `051a8a0`, starts the capture
upstream before OGX, starts the real `uv run ogx go` process, waits for both
readiness endpoints, and drives the public Messages route. Its default output
is a fresh temporary directory. `--refreeze` is required to replace checked-in
evidence.

- Expected behavior: map adaptive thinking to an explicit target-dialect
  reasoning configuration, or reject the unsupported request. OGX documents a
  clear error for thinking in translation mode, and PR 5938 deliberately
  established rejection rather than silent loss.
- Observed behavior: adaptive thinking returns HTTP 200 in 5/5 trials. Each
  forwarded request contains the user message but no `thinking`, `reasoning`,
  or `reasoning_effort` key.
- Raw client request bytes:
  `transcripts/081/raw/adaptive-thinking-*-client-request.json`
- Raw client response bytes:
  `transcripts/081/raw/adaptive-thinking-*-client-response.json`
- Raw forwarded request bytes:
  `transcripts/081/raw/forwarded-*-request.json`
- Raw upstream response bytes:
  `transcripts/081/raw/upstream-*-response.json`
- N/N summary: `transcripts/081/ogx-adaptive-thinking-cases.json`
- Reproduction rate: 5/5
- Smallest trigger: change only `thinking.type` from the rejected `enabled`
  variant to the accepted `adaptive` variant

### Control

The control uses the same route, model, prompt, process, and capture upstream.
It changes only the thinking object to:

```json
{"type":"enabled","budget_tokens":1024}
```

OGX returns HTTP 400 with an Anthropic `invalid_request_error` in 5/5 trials
and sends no upstream request. The raw client bytes are under
`transcripts/081/raw/enabled-thinking-control-*`; the N/N summary is
`transcripts/081/ogx-enabled-thinking-control-cases.json`.

This isolates the defect to OGX's handling of the adaptive union variant. The
same public route and translation function already fail closed for another
thinking variant that cannot be represented safely.

### Live direct-provider control

The direct control called Anthropic's public `/v1/models` and `/v1/messages`
endpoints on 2026-09-22 using `ANTHROPIC_API_KEY` from the ignored local
`.env`. The selected `claude-opus-4-8` model advertised adaptive thinking, and
the exact synthetic request with `thinking.type == "adaptive"` returned a
structurally valid HTTP 200 message in 5/5 trials.

```sh
set -a
source ./.env
set +a
python3 transcripts/081/live_anthropic_control.py --trials 5
```

Sanitized raw request and response bytes are in
`transcripts/081/live-anthropic-*.http`; the exact N/N result is in
`transcripts/081/live-anthropic-summary.json`. API keys and private request
and tenant identifiers are replaced inline. Response prose is not compared
because it is nondeterministic. The structural contract and HTTP status are
compared.

### False-positive checks

- [x] Tested the exact adaptive-thinking claim.
- [x] Pinned and ran OGX v1.4.0 commit `051a8a0` locally.
- [x] Used OGX's real public endpoint and installed dependencies.
- [x] Ruled out malformed input with Anthropic's live endpoint, which accepted
      the same adaptive object 5/5.
- [x] Ruled out model nondeterminism by checking structural wire invariants.
- [x] Used a same-route control that changes only the thinking variant.
- [x] Saved raw request, response, and forwarded bodies under `transcripts/`.
- [x] Sanitized the live captures and verified that the credential bytes are
      absent.

## Gate 2: usefulness

### Who gets bitten

- Affected user: an Anthropic SDK or Claude Code user who selects adaptive
  thinking while OGX routes the request to an OpenAI-compatible provider
- Real workflow: the client points its documented Anthropic Messages flow at
  OGX and expects either the requested reasoning policy or an actionable error
- Preconditions: OGX translation mode and an accepted adaptive thinking object
- Conditional frequency: silent loss reproduced 5/5 once those conditions
  hold; production frequency of adaptive requests was not measured

### Observable consequence

`client requests adaptive thinking -> OGX returns success -> OGX removes the
only thinking configuration -> backend runs under its default reasoning policy
-> client receives a normal success and cannot fall back or report the missing
constraint`

- User action: send a supported Anthropic adaptive-thinking request through
  OGX's documented Messages compatibility route.
- Wire-level defect: the exact forwarded body has no thinking or reasoning
  configuration.
- Consumer-visible failure: the public Anthropic endpoint returns HTTP 200
  instead of the fail-closed error that would let the client retry a native
  provider or disable the unsupported feature explicitly.
- Consumer-boundary evidence: each
  `adaptive-thinking-*-client-response.json` is a successful Anthropic message,
  while its matched `forwarded-*-request.json` lacks the requested constraint.
- Measured impact: loss of the requested configuration and false success in
  5/5 trials.
- Inferred impact: output quality, token use, and task success may change when
  a backend defaults to less reasoning. Those downstream magnitudes were not
  measured.

### Bug or not

- Expected behavior is the intended spec: OGX's API schema includes the
  `adaptive` union variant; its Messages documentation says thinking is mapped
  to OpenAI equivalents; its Claude Code documentation promises a clear error
  for thinking in translation mode; and PR 5938 deliberately implements that
  error for `enabled`.
- Maintainer ruling: PR 5938 rules that unsupported thinking in translation
  mode must fail closed. No issue, pull request, test, UI behavior, or comment
  was found that exempts `adaptive` or makes silent removal intentional.
- Supported usage: OGX documents the official Anthropic SDK with any model and
  documents OpenAI-compatible translation. Its request model accepts
  `adaptive`, and Anthropic's live model metadata advertised that variant.
- Boundary crossed: this is not a disclosure claim. OGX acknowledges a client
  constraint at its public API boundary but omits it at the provider boundary.
- Maintainer fix: reject adaptive thinking in translation mode unless OGX can
  map it to an explicit target-provider reasoning configuration.
- Label: `bug`

## Gate 3: upstream status

- Date checked: 2026-09-22
- Upstream release checked: v1.4.0, released 2026-09-11, commit `051a8a0`
- Search terms: `adaptive thinking`, `thinking translation mode`, `thinking
  config dropped`, and `thinking.type adaptive`
- Issues and pull requests searched: open and closed GitHub issues and pull
  requests in `ogx-ai/ogx`
- Releases and documentation searched: latest GitHub release, Anthropic
  Messages API page, conformance report, Claude Code integration page, release
  notes, and the v1.4.0 source tree
- Relevant commits searched: history and current implementation of
  `anthropic_translation.py` and `models.py`
- Matching links: [PR 5938](https://github.com/ogx-ai/ogx/pull/5938) covers
  `enabled`; [PR 5386](https://github.com/ogx-ai/ogx/pull/5386) introduced the
  Messages route. Neither covers adaptive translation loss.

Classification:

- [x] Novel
- [ ] Duplicate, open and still reproducible
- [ ] Fixed on current release
- [ ] Regression
- [ ] Documented behavior
- [ ] Discussed upstream without a dedicated ticket
- [ ] Incomplete, current upstream state could not be verified

The nearby merged work establishes the expected fail-closed behavior, but the
adaptive variant remains accepted and silently removed on the current release.

## Frozen invariant

- Issue writeup: `issues/081-ogx-messages-translation-losses/README.md`
- Checker: `ogx_adaptive_thinking_loss` in
  `crates/harness/src/checks.rs`
- Conformance coverage:
  `ogx_adaptive_thinking_is_silently_ignored` and
  `ogx_enabled_thinking_control_fails_closed`
- Invariant: every accepted adaptive request must forward an explicit thinking
  or reasoning configuration; rejecting an unsupported request is conformant.
  The checker validates every trial, so one conformant trial cannot hide a
  mixed failing set.

## Validation

| Check | Command | Result |
|---|---|---|
| Reproduction | `python3 transcripts/081/hunt.py "$OGX_SRC"` | adaptive 5/5 HTTP 200 with capture; enabled 5/5 HTTP 400 |
| Live control | `python3 transcripts/081/live_anthropic_control.py --trials 5` | adaptive advertised; 5/5 HTTP 200 structural messages |
| Harness | `cargo test --workspace` | 36 unit and 157 conformance tests passed |
| Formatting | `cargo fmt --all -- --check` | passed |
| Lint | `cargo clippy --workspace --all-targets -- -D warnings` | passed |
| README counts | `python3 tools/update-readme-counts.py --check` | 59 bugs and 193 tests |

## Security and scope

- [x] No API key, credential, private prompt, or unsanitized response is
      committed.
- [x] Only environment variable names appear in commands and documentation.
- [x] The pull request contains only Finding 081, plus a base-branch build
      repair. `origin/main` at `cd7b401` does not compile: a previous conflict
      resolution spliced `executed_tool_results_preserved` into the body of
      `image_url_cache_key_case_sensitive`. The merge commit moves it back to
      module level without changing behavior. No checker, test, or scoreboard
      row from Finding 080 or Finding 082 is added or removed.
- [x] Unrelated generated files and local state are excluded.
- [x] No Finding 080 or Finding 082 path appears in the base-to-head diff.

## Author verdict

- Correctness: PASS
- Usefulness: PASS
- Upstream status: PASS
- Overall: ACCEPT

## Independent review

Run `.github/agents/kairo-reproduction-reviewer.agent.md` against this pull
request. Approval remains blocked until a reviewer independently reruns the
critical path and tries to falsify all three gates.
