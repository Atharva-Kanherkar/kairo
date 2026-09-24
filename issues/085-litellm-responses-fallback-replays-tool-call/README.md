# 085, LiteLLM Responses mid-stream fallback replays a delivered tool call, and Codex runs it twice

- **Upstream**: [BerriAI/litellm](https://github.com/BerriAI/litellm). No matching issue or pull request on 2026-09-24. The governing maintainer ruling is merged [#34627](https://github.com/BerriAI/litellm/pull/34627), which makes the chat-completions path re-raise instead of retrying once any content has streamed. Related work is listed under "Upstream status".
- **Tool under test**:
  - LiteLLM 1.102.1, the current latest release ([tag v1.102.1](https://github.com/BerriAI/litellm/releases/tag/v1.102.1)).
  - Unreleased main at [`8bbe7edb`](https://github.com/BerriAI/litellm/commit/8bbe7edb711ee894deb5ad7e3cc10391a9837de9), which reports `1.104.0`. Its `router.py` is byte-identical to `1c8a0ff6`, which the first version of this report used.
  - Consumers: the Codex CLI 0.156.1, and the OpenAI Python SDK 2.54.0, 3.13.0, 3.14.0, and 3.19.2.
- **Reproduced**: 2026-09-24.
  - The real LiteLLM proxy CLI serves `POST /v1/responses` with a real `router_settings.fallbacks` configuration, in front of a deterministic OpenAI Responses upstream.
  - One Codex case uses the live OpenAI API (`gpt-5.4-nano`) as the fallback deployment.

## What breaks

An agent sends Responses API traffic through LiteLLM with a fallback configured. The primary streams a completed tool call, then fails with a retriable in-band event: an `error` event, or `response.failed` with `server_error`.

LiteLLM decides whether anything was already delivered by checking only accumulated **text** (`router.py:3274`). A completed `function_call` is not text, so the wrapper treats the turn as if nothing reached the client:
- It replays the original request against the fallback deployment.
- It appends the fallback's whole second lifecycle to the same public SSE body, restarting `output_index` at 0.

```text
Client -> LiteLLM /v1/responses (fallbacks: primary -> backup)
       -> primary: function_call "echo run >> ledger.txt" delivered, then an in-band 5xx
       -> generated_content is text-only, sees "" -> replays the original input
       -> backup: the same call again, its own lifecycle, output_index from 0
Client <- one SSE body: two response.created, two completed calls, no error
```

The measured consequences:

- **Codex runs the side effect twice.** The real Codex CLI 0.156.1 runs a side-effecting shell command twice, with exit 0 and no error shown to the user. This happens in 5/5 runs on both LiteLLM versions, and in 5/5 runs with a live `gpt-5.4-nano` fallback. Codex's own retries are disabled, and both controls run the command once. Codex dispatches a tool on every completed call item and treats a second `response.created` as an id update ([`codex-rs/core/src/session/turn.rs`](https://github.com/openai/codex/blob/c098f97e5305394c7f30a876c1393cf34a3eedca/codex-rs/core/src/session/turn.rs#L2597-L2704)). Any consumer that runs tools on `response.output_item.done` is exposed the same way.
- **Final-response consumers are not affected.** The final response that the OpenAI SDK assembles contains only the fallback's call, in every trigger record, so a consumer that runs tools only from `get_final_response()` runs the call once.
- **openai-python below 3.14.0 crashes.** `client.responses.stream()` raises `AssertionError` before any final response when the item type at the reused index changes. That happens with a reasoning-model fallback, which leads with a `reasoning` item, or after a partial text answer. openai-python 3.14.0 (2026-09-14, [#3126](https://github.com/openai/openai-python/issues/3126)) keys its accumulator by `output_index` and tolerates the reuse. All 2.x releases and 3.0.0 through 3.13.0 crash, 5/5; 3.14.0 and 3.19.2 do not, 5/5. LiteLLM itself pins `openai<3`, so any Python environment that also installs LiteLLM gets a crashing SDK.

What triggers it, as measured:
- **Triggers:** only a retriable in-band failure event after at least one output item was forwarded.
- **Transport drop:** the upstream closes the connection early and LiteLLM does not fall back. It forwards the delivered call and then an error, as `boundary-transport-drop` shows.
- **Failure before any output item:** takes the conformant path, as `control-fault-before-output` shows.

How often production streams hit an in-band retriable failure after a tool call was not measured.

## Wire evidence

All files are under `transcripts/085/`, with five independent trials each:
- `1.102.1/` and `1.104.0/`: the real `openai` SDK, driven as a subprocess, calls the real proxy through a loopback relay. Each record keeps:
  - `client_request` and `client_response`: the exact public bytes.
  - `upstream_exchanges`: the exact bytes LiteLLM sent to and received from the deterministic upstream.
  - `consumer`: the SDK's own outcome, under 2.54.0.
  - `sdk_replays`: the same public body replayed byte for byte under openai 3.13.0, 3.14.0, and 3.19.2.
- `codex/`: the Codex runs. See [`codex/README.md`](../../transcripts/085/codex/README.md).

- **Upstream sent** (`upstream_exchanges`):
  - The primary: `response.created`, then a completed `function_call` (`call_primary`), then `error` with `server_error`.
  - The fallback: its own complete stream (`call_fallback`).
- **LiteLLM emitted** (`client_response.body_raw`): both lifecycles in one body.
  - `response.created` for the primary, and `call_primary` completed at `output_index` 0.
  - `response.created` for the fallback, and `call_fallback` completed at `output_index` 0.
  - `response.completed` for the fallback. The primary's error is never shown to the client.
- **Expected** after delivered output: the failure is surfaced and no second lifecycle starts, which is what the chat path has done since #34627. The client receives `call_primary` and then a terminal `response.failed` or `error`. LiteLLM main already emits exactly that shape for a transport drop (`1.104.0/boundary-transport-drop.jsonl`): `call_primary`, then `response.failed` with `server_error`, then `[DONE]`, with no fallback call.

| Mode | Primary behavior | Upstream calls | Public stream | SDK 2.54.0 / 3.13.0 | SDK 3.14.0 / 3.19.2 |
|---|---|---:|---|---|---|
| `trigger-duplicate-tool` | completed call, then `error` | 2 | two lifecycles, index 0 reused | two completed calls | two completed calls |
| `trigger-duplicate-tool-response-failed` | completed call, then `response.failed` | 2 | same | two completed calls | two completed calls |
| `trigger-sdk-crash-item-type` | completed call, then `error`; fallback leads with `reasoning` | 2 | same, item type changes at index 0 | `AssertionError` | two completed calls |
| `trigger-sdk-crash-partial-text` | partial text, then `error` (`tool_choice: "auto"`) | 2 | continuation, index 0 reused | `AssertionError` | one completed call |
| `control-no-fault` | completes | 1 | one lifecycle | one call | one call |
| `control-fault-before-output` | `error` before any output item | 2 | two `response.created`, no item before the second | one call | one call |
| `boundary-announced-only` | announces a call, then `error` | 2 | index 0 reused | one call | one call |
| `boundary-transport-drop` | completed call, then the connection closes | 1 | no fallback, error surfaced | error | error |

Every row is 5/5 on both LiteLLM versions. The two boundary rows mark the edges of the claim:
- **Announced only:** one announced but never completed item is enough to reuse the index, but no tested consumer sees a failure.
- **Transport drop:** does not reach the fallback at all.

The evidence is sanitized:
- Only `content-type` and `x-litellm-version` headers are kept.
- Both rigs reject fixed local keys and any token-shaped `sk-` string before writing.
- The Codex rig also rejects the value of `OPENAI_API_KEY` if it appears in any capture.

## Control

- `control-no-fault`: the primary completes. It makes one upstream call, the stream has one lifecycle, and Codex runs the command once.
- `control-fault-before-output`: the primary fails after `response.created` and before any output item. This is the same code branch as the trigger. LiteLLM had already forwarded `response.created`, so `is_pre_first_chunk` is false, and the replay happens because `generated_content` is empty. The only difference from the trigger is the delivered call. LiteLLM replays, the fallback's call is the only call, and Codex runs the command once.

The route, tools, `tool_choice`, fallback configuration, client, and SDK are the same across trigger and controls. `num_retries: 0` keeps the upstream call counts deterministic. With LiteLLM's default retry setting the trigger replays the same way, which was checked during review but is not committed.

The bytes are the same on the pinned release and on main. They also stay the same whether the primary sends an `error` event or `response.failed`, and whether the fallback is deterministic or live.

## Root cause

Line numbers are for 1.102.1, with main in parentheses.

- **The Responses wrapper:** `Router._aresponses_streaming_iterator` (`litellm/router.py:3101`, main `:3186`) wraps the Responses streaming path. It was added in [#28215](https://github.com/BerriAI/litellm/pull/28215) on 2026-05-20, whose description promises "full parity with the chat-completions path".
- **The replay decision:**
  - On `MidStreamFallbackError`, the wrapper replays the original input when `e.is_pre_first_chunk or not e.generated_content` (`router.py:3274`, main `:3354`).
  - `generated_content` grows only from `response.output_text.delta` events (`litellm/responses/streaming_iterator.py:344-347`, main `:383-386`). A completed `function_call` or `reasoning` item never counts.
  - After partial text, the wrapper instead sends a continuation prompt through `_build_responses_continuation_input` (`router.py:3041`).
- **The splice:** either way, it forwards the fallback's events unchanged (`router.py:3308`, main `:3395`), so the public stream carries a second lifecycle.
- **The chat-completions path now does the opposite.** [#34627](https://github.com/BerriAI/litellm/pull/34627), merged 2026-08-04, made `_acompletion_streaming_iterator` re-raise the original error instead of falling back "once any content (text or non-text) already streamed". It detects content, including tool-call deltas, with `_stream_chunks_have_generated_content` (`router.py:402` and `:2825-2830`). Its description gives the reason: "retrying in that case would otherwise send a second, unrelated response after content the client already received".
- **The gap:** #34627 did not touch the Responses wrapper, which still falls back after a delivered tool call. So the gap is a missing port of that rule, not a deliberate difference.

## Bug or not

- **The expected behavior is the maintainers' current contract, not a stale doc line.**
  - #34627's re-raise rule is merged code with tests, and covers text and non-text content alike.
  - On 2026-09-19 maintainer mateo-berri closed [#31067](https://github.com/BerriAI/litellm/issues/31067) and [#27967](https://github.com/BerriAI/litellm/issues/27967), citing it. The closing comment on #27967 reads: "PR #34627 (v1.97.0+) removed the prefix continuation, so a mid-stream failure now re-raises the original error."
  - The "full parity" sentence in #28215 is older than that ruling.
- **Maintainer ruling checked.** No commit, PR, or comment accepts a replayed call or a second lifecycle. Merged [#40121](https://github.com/BerriAI/litellm/pull/40121), for [#40118](https://github.com/BerriAI/litellm/issues/40118), fixed the same two-lifecycles-in-one-stream shape on the MCP auto-execution path.
- **Supported usage.**
  - `router_settings.fallbacks` with the Responses API is first-party functionality.
  - Codex with a custom `responses` provider is a normal client.
  - The trigger is a retriable `server_error` from the provider, and the client input is valid.
- **Boundary.** This is not a disclosure claim. Valid input and two valid provider streams become a public stream on which a real agent repeats a side effect.
- **Maintainer fix, in one sentence**: once `_aresponses_streaming_iterator` has forwarded any output item, re-raise the original error instead of falling back, as `_acompletion_streaming_iterator` has done since #34627.

Classification label: `bug`.

## Upstream status

Checked on 2026-09-24 against release 1.102.1 and main `8bbe7edb`.

Searches covered issues and pull requests, open and closed. Terms:
- `MidStreamFallbackError`, `generated_content`, `aresponses_streaming_iterator`, `_build_responses_continuation_input`
- `mid-stream fallback`, `responses fallback tool call`, `responses fallback duplicate`, `fallback tool twice`, `fallback replay responses`
- `output_item fallback`, `two response.created`, `responses stream AssertionError`
- Recent `fallback` results created since 2026-09-22.

No report covers a Responses fallback after a delivered output item. The closest results:

- [#28215](https://github.com/BerriAI/litellm/pull/28215) (merged): added the Responses wrapper.
- [#34627](https://github.com/BerriAI/litellm/pull/34627) (merged): the chat-path re-raise rule. It is the ruling this report relies on, and it did not touch the Responses wrapper.
- [#31067](https://github.com/BerriAI/litellm/issues/31067) and [#27967](https://github.com/BerriAI/litellm/issues/27967) (closed as not planned): chat-path continuation problems, closed by citing #34627.
- [#40118](https://github.com/BerriAI/litellm/issues/40118) / [#40121](https://github.com/BerriAI/litellm/pull/40121) (fixed): the same lifecycle splice on the MCP path only.
- [#42283](https://github.com/BerriAI/litellm/pull/42283) (merged 2026-09-21): walks every fallback entry after a mid-stream failure. It does not change what counts as delivered.
- [#31089](https://github.com/BerriAI/litellm/pull/31089), [#41127](https://github.com/BerriAI/litellm/pull/41127), and [#42736](https://github.com/BerriAI/litellm/pull/42736) (open): chat-path continuation and fallback shape. None touches the Responses wrapper.

Classification: `novel`.

## Test

Three checkers in `crates/harness/src/checks.rs` state the invariant from three angles:

- `responses_no_restart_after_output`: once a public stream has announced an output item, no later `response.created` may start another lifecycle. A second `response.created` before any output item is conformant, which covers the fault-before-output control.
- `responses_fallback_not_spliced_after_delivery`: a cross-hop check. If an upstream attempt ends without completing after the client received one of its items, no item from a later attempt may appear in the public stream. It holds however the gateway renumbers indexes or merges lifecycles. `issue_085_splice_invariant_survives_renumbering` proves this on a real capture that was rewritten to hide the second lifecycle.
- `responses_fallback_preserves_delivered_indexes`: an `output_index` assigned to one item is never reassigned to a different item. This is the collision that crashes openai-python below 3.14.

The conformance suite checks every one of the five records in every mode, version, and Codex case. It covers:
- provenance, the public route, and the request shape
- upstream call counts
- all three checker verdicts
- every SDK version's outcome
- for Codex, the measured side-effect count, Codex's returned tool results, and that each shared reference file exists

Mutating the fifth trial or appending a malformed line fails the suite.

## Reproduction

Python 3.12 is required. The captures used 3.12.13.

```sh
uv venv /tmp/kairo-085-litellm-1102 --python 3.12
uv pip install --python /tmp/kairo-085-litellm-1102/bin/python -r transcripts/085/requirements-1.102.1.txt
for v in 3.13.0 3.14.0 3.19.2; do
  uv venv /tmp/kairo-085-openai-$v --python 3.12
  uv pip install --python /tmp/kairo-085-openai-$v/bin/python openai==$v
done
/tmp/kairo-085-litellm-1102/bin/python -B transcripts/085/reproduce.py \
  --python /tmp/kairo-085-litellm-1102/bin/python --expect-litellm 1.102.1 \
  --captured-at 2026-09-24 --output-dir /tmp/kairo-085-output-1102 \
  --sdk-python /tmp/kairo-085-openai-3.13.0/bin/python \
  --sdk-python /tmp/kairo-085-openai-3.14.0/bin/python \
  --sdk-python /tmp/kairo-085-openai-3.19.2/bin/python

# current main (unreleased; install from a checkout)
git clone https://github.com/BerriAI/litellm.git /tmp/litellm-085-main
uv venv /tmp/kairo-085-litellm-main --python 3.12
uv pip install --python /tmp/kairo-085-litellm-main/bin/python -e "/tmp/litellm-085-main[proxy]" openai==2.54.0
# rerun reproduce.py with --python /tmp/kairo-085-litellm-main/bin/python --expect-litellm 1.104.0

# Codex consumer boundary: see transcripts/085/codex/README.md

python3 -B -m unittest transcripts/085/test_reproduce.py transcripts/085/codex/test_run_codex_matrix.py
python3 transcripts/085/codex/verify_refs.py transcripts/085/codex/1.102.1 transcripts/085/codex/1.104.0 \
  transcripts/085/codex/1.102.1-live --shared transcripts/085/codex/shared
cargo test --workspace
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
python3 tools/update-readme-counts.py --check
```

Use fresh output directories. Both runners refuse an existing output directory, so a rerun never overwrites committed evidence. The wire rig uses loopback ports only and reads no credentials.
