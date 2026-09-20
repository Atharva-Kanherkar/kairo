# Finding

## Claim

Switchyard loses locally materialized history when a Responses client continues a cross-format Chat conversation by `conversation`, although the equivalent `previous_response_id` continuation preserves it.

- Upstream project: [NVIDIA-NeMo/Switchyard](https://github.com/NVIDIA-NeMo/Switchyard)
- Cited upstream report: none; regression-adjacent gap in [PR #721](https://github.com/NVIDIA-NeMo/Switchyard/pull/721) and [PR #781](https://github.com/NVIDIA-NeMo/Switchyard/pull/781)
- Target type: open-source/local
- Tested commit: [`bfcd023cb6791efd8ac59d32afb5095c74fef1ff`](https://github.com/NVIDIA-NeMo/Switchyard/commit/bfcd023cb6791efd8ac59d32afb5095c74fef1ff), current `main` on 2026-09-20
- Client dialect and endpoint: OpenAI Responses, `POST /v1/responses`
- Backend dialect: OpenAI Chat Completions, deterministic local capture upstream
- Model: synthetic `model/chat`
- Relevant configuration: documented passthrough route, `max_retries = 0`, no credentials

## Gate 1: correctness

### Exact reproduction

```text
git clone https://github.com/NVIDIA-NeMo/Switchyard.git
cd Switchyard
git checkout bfcd023cb6791efd8ac59d32afb5095c74fef1ff
rustup run 1.96.1 cargo build --release -p switchyard-server
cd /path/to/kairo
transcripts/081/run_repro.sh /path/to/Switchyard/target/release/switchyard-server
```

- Expected behavior: the second request using the same `conversation` ID reaches the Chat backend with the seed user turn and seed assistant answer, like the response-ID control.
- Observed behavior: the conversation follow-up reaches the Chat backend with only `RECALL`; Switchyard returns HTTP 200 and a plausible answer with no seed context.
- Raw request and response evidence: [`capture-bug.jsonl`](../../transcripts/081/capture-bug.jsonl)
- Forwarded-request evidence: [`forwarded.jsonl`](../../transcripts/081/forwarded.jsonl), exact UTF-8 bodies read by the capture upstream
- Reproduction rate: conversation history missing 5/5; `previous_response_id` control preserved it 5/5
- Smallest isolated trigger: replace the working follow-up's `previous_response_id` with the supported `conversation` ID.

### Hosted closed-source evidence

N/A. The real open-source release server ran locally from a pinned checkout.

### Control

```text
# run_repro.sh sends the same seed and recall through the same route.
# Only the continuation field changes to previous_response_id.
```

- Control result: 5/5 forwarded Chat requests contained the seed user turn, seed assistant answer, and recall turn; 5/5 client responses contained `SEED_CANARY_081`.
- Attribution: server, backend dialect, route, model, prompt, and upstream are identical. Only the supported continuation selector changes.

| Rung | Result | Evidence |
|---|---|---|
| Direct provider | N/A, Chat has no Responses continuation fields | Capture upstream requires full Chat history |
| Plain or bypass path | `previous_response_id` preserves history 5/5 | `capture-control.jsonl`, `forwarded.jsonl` |
| Routed path | `conversation` loses history 5/5 | `capture-bug.jsonl`, `forwarded.jsonl` |
| Trigger removed | Replacing `conversation` with the returned response ID restores history | Same files |

### False-positive checks

- [x] Tested the exact conversation continuation behavior promised by PR #721.
- [x] Pinned current upstream `main` and supported Rust 1.96.1.
- [x] Used the documented passthrough route and valid Responses requests.
- [x] Model nondeterminism is absent; the upstream deterministically echoes exact forwarded history.
- [x] The real `switchyard-server` public HTTP entry point produced the defect.
- [x] Evidence contains synthetic canaries only.

## Gate 2: usefulness

### Who gets bitten

- Affected user: an application using OpenAI Responses conversation state through Switchyard with a Chat or Anthropic answer backend.
- Real workflow: create or reuse a conversation, send a seed turn, then send only new input with the same `conversation` ID.
- Preconditions and frequency: cross-format Responses ingress plus `conversation`; measured 5/5. Native Responses state and full-history callers are outside this claim.

### Observable consequence

- User action: asks a second-turn question by conversation ID without retransmitting history.
- Wire defect: Switchyard forwards only the new turn to the Chat backend.
- End-user failure: the answer is generated without prior instructions, facts, tool calls, or results, while the API returns HTTP 200.
- Consumer boundary: the deterministic backend echoes all received turns. The conversation response contains only `RECALL`; the response-ID control contains `SEED_CANARY_081` and `RECALL`.
- Measured impact: 5/5 complete history losses at both upstream-request and client-response boundaries.
- Inferred impact: real agents can forget prior tool calls or constraints and return a confident but context-free answer.

### Bug or not

- Expected behavior: PR #721 explicitly says clients may use `previous_response_id` or `conversation`, and its merged tests make both return to stored provider state. PR #781 promises canonical history for cross-format stored Responses continuations.
- Maintainer ruling searched: no commit, issue, PR, test, or documentation classifies cross-format `conversation` loss as intended. PR #781 tests only `previous_response_id`.
- Trigger is supported usage: `conversation` is documented in `docs/routing_algorithms/llm_classifier_routing.md` and PR #721.
- Boundary crossed: this is correctness, not disclosure. The conversation-state boundary promises prior turns are part of the continuation; the backend receives none.
- Fix: pass the request conversation ID into `remember_canonical_response`, store the same materialized history under both eligible response and conversation IDs, and add cross-format buffered and streamed conversation tests.
- Label: `bug`

## Gate 3: upstream status

- Date checked: 2026-09-20
- Upstream commit checked: `bfcd023cb6791efd8ac59d32afb5095c74fef1ff`, current `main`
- Search terms: `previous_response_id`, `conversation continuation`, `canonical history`, `state owner`, `materialize`, `conversation history`
- Issues searched: all open Switchyard issues, including #583; no match
- Pull requests searched: #709, #713, #721, #781, #796 and repository-wide search
- Releases, changelog, and docs: v0.2.0, `CHANGELOG.md`, `docs/routing_algorithms/llm_classifier_routing.md`
- Relevant commits: merged #721 and #781; #781 introduced local canonical history on 2026-09-18
- Matching links: [#721](https://github.com/NVIDIA-NeMo/Switchyard/pull/721), [#781](https://github.com/NVIDIA-NeMo/Switchyard/pull/781)

Classification:

- [x] Novel
- [ ] Duplicate, open and still reproducible
- [ ] Fixed on current release
- [ ] Regression
- [ ] Documented behavior
- [ ] Discussed upstream without a dedicated ticket
- [ ] Incomplete, current upstream state could not be verified

The prior work covers provider-owned conversation routing and cross-format response-ID materialization. No upstream artifact covers the missing cross-format conversation record.

## Root cause

`crates/libsy-llm-client/src/run.rs` looks up either `previous_response_id` or `conversation` in one state map. Native Responses recording passes both IDs to `remember`. Cross-format `remember_canonical_response`, however, accepts only the response and never receives the request's conversation ID. It calls `remember(response_id, None, ...)`, so the later conversation lookup misses and routing silently continues without history.

## Frozen invariant

- Issue writeup: this file
- Checker: `response_conversation_preserves_history` in `crates/harness/src/checks.rs`
- Conformance tests: bug and control cases in `crates/harness/tests/conformance.rs`
- Why invariant-level: every forwarded recall selected by a stored conversation must include its seed canary. The checker does not depend on Switchyard function names.

## Validation

| Check | Command | Result |
|---|---|---|
| Reproduction | `transcripts/081/run_repro.sh SWITCHYARD_BIN` | PASS, bug 0/5 preserved |
| Control | same command, response-ID matrix | PASS, 5/5 preserved |
| Harness | `cargo test --workspace` | PASS, 180 tests |
| Formatting | `cargo fmt --all -- --check` | PASS |
| Lint | `cargo clippy --workspace --all-targets -- -D warnings` | PASS |
| README counts | `python3 tools/update-readme-counts.py --check` | PASS, 57 folders and 180 tests |

## Security and scope

- [x] No API key, credential, private prompt, or unsanitized provider response is committed.
- [x] No environment secret is required.
- [x] The pull request contains one finding.
- [x] Unrelated files are excluded.

## Author verdict

- Correctness: PASS
- Usefulness: PASS
- Upstream status: PASS
- Overall: ACCEPT

## Independent review

Run `.github/agents/kairo-reproduction-reviewer.agent.md` against this pull request. Approval remains blocked until the reviewer independently reruns the critical path and all three gates pass.
