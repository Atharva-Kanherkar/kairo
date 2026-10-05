# 094, Dynamo's Anthropic Messages endpoint stops on a requested stop sequence but reports `end_turn` with `stop_sequence: null`, so clients cannot tell which delimiter fired

- **Upstream**: [ai-dynamo/dynamo](https://github.com/ai-dynamo/dynamo), no matching issue or pull request. Classification: `novel` as of 2026-10-06 (rechecked with the same terms against `main` `062787028599`, 2026-10-05 18:32 UTC; no new issue, pull request, or commit to `lib/llm/src/protocols/anthropic/`, and the defective lines below are unchanged). Search terms, run on 2026-10-05 against issues and pull requests in `ai-dynamo/dynamo` and `ai-dynamo/frontend-crates`: `stop_sequence anthropic`, `stop_reason stop_sequence`, `anthropic stop_sequences end_turn`, `stop_sequence null`, `stop_reason end_turn stop sequence`, `stop_sequence`, `stop_reason anthropic`. The only hits were [#12472](https://github.com/ai-dynamo/dynamo/pull/12472) (cache-creation usage, merged) and [#8284](https://github.com/ai-dynamo/dynamo/pull/8284) (closed unmerged 2026-07-08). #8284 planned a replay test, scenario A8 `stop_sequence_streaming`, described as "`stop_reason: stop_sequence` carried through the stream converter". That test never landed. The five most recent commits to `lib/llm/src/protocols/anthropic/types.rs` (#14755, #12624, #14605, #13792, #13791) do not touch this mapping, and none of the open pull requests with "anthropic" in the title do either.
- **Tool under test**: NVIDIA Dynamo frontend (`python -m dynamo.frontend --enable-anthropic-api`). Reproduced on the nightly wheels `ai-dynamo==1.6.0.dev20261004` / `ai-dynamo-runtime==1.6.0.dev20261004`, and on the current release `1.5.0`. The defective lines are present on upstream `main` at `d39d11afe331` (2026-10-05 08:23 UTC). Container: `python:3.12-slim`, linux/aarch64.
- **Reproduced**: 2026-10-05 UTC. The committed Dynamo, release, and consumer captures were recorded 2026-10-05 19:28 to 19:30 UTC with an engine that records what it actually emitted. Model card `Qwen/Qwen3-0.6B` (tokenizer and template only), served by a deterministic engine written against Dynamo's public `LLMEngine` backend API. Parsers: `--tool-call-parser hermes --reasoning-parser qwen3`. Provider control: live Anthropic Messages API, `claude-haiku-4-5`, credential read from `ANTHROPIC_API_KEY`.
- **Result**: **8/8** Dynamo `/v1/messages` exchanges (5 unary, 3 streamed) return `stop_reason: "end_turn"`, `stop_sequence: null` after stopping on `</answer>`. The live Anthropic API returns `stop_reason: "stop_sequence"`, `stop_sequence: "</answer>"` for the same request and the same returned text, **8/8**. The engine records show the frontend received the whole `</answer>` delimiter in every bug case (13 of 20 planned tokens emitted before the frontend cancelled the stream). Anthropic's own `tool_use_package` `ToolUser` fails **5/5** against Dynamo, with 0 tool executions. It succeeds **5/5** when only these two fields are set the way the API reports them.

## What breaks

The Anthropic Messages contract says the response explains why generation stopped. When a caller passes `stop_sequences` and the model produces one of them, the API returns `stop_reason: "stop_sequence"` and puts the matched string in `stop_sequence`. The matched text is excluded from `content`, so these two fields are the only place a client learns that a delimiter fired, and which one.

Dynamo applies the stop sequence correctly: generation stops and the delimiter is removed from the text. It then reports the stop as an ordinary `end_turn` and always sends `stop_sequence: null`. The streamed `message_delta` omits `stop_sequence` entirely. A delimiter stop and a natural end of turn are indistinguishable on `/v1/messages`. With two or more stop sequences, the client cannot know which one ended the turn.

Who it hurts: any client that drives an agent or a structured protocol through stop sequences on the Messages API and points at Dynamo's documented Anthropic endpoint. The concrete case measured here is Anthropic's own `anthropic-tools` `ToolUser` (the legacy XML function-calling package). It calls `messages.create(stop_sequences=["</function_calls>", "\n\nHuman:"])` and, when `stop_sequence == "</function_calls>"`, re-appends the closing tag before parsing the call. Against Dynamo the tag is never restored. The parser rejects the call with "No valid <function_calls></function_calls> tags present in your query.", retries, and raises `ValueError: Hit maximum number of retries attempting to use tools`. The tool never runs. The same re-append idiom appears in the NYU LLM-CTF baseline agent (`nyuctf_baseline/backends/anthropic_backend.py:77-78`). OpenPipe ART reads `response.stop_sequence` to mark stop tokens when it tokenizes Messages trajectories for RL training (`src/art/trajectories/_tokenize.py:4219-4220`). Those two were traced in source, not run.

Frequency: every `/v1/messages` request whose generation ends on a requested stop sequence, unary and streamed. No configuration avoids it. `--enable-anthropic-api` is the documented way to serve Anthropic clients ([agent harnesses guide](https://github.com/ai-dynamo/dynamo/blob/main/docs/fern/pages/use-cases/agents/agent-harnesses.mdx)).

Measured versus inferred: the wrong fields (8/8), the provider's correct fields (8/8), the same frontend knowing the matched string (3/3), and the `ToolUser` failure with zero tool executions (5/5) are **measured**. The NYU and ART consequences are **inferred** from their source. Claude Code was not tested and is not known to depend on `stop_sequence`.

## Wire evidence

All under `transcripts/094/`. Client-side bytes were recorded by a reverse proxy in front of the frontend. Each Dynamo exchange, on both versions, has a matching engine-side record under `forwarded/`. It holds the request the frontend forwarded (token ids and stop conditions), the planned completion (`planned_completion`), what the engine actually handed to the Dynamo runtime (`emitted_payloads`, `emitted_token_ids`, `emitted_text`), and how generation ended (`termination`). `forwarded/lifecycle.jsonl` summarizes planned and sent token counts. The planned completion is only the script. The captures and the checker use the emitted text.

### Bug case

Request (`dynamo/0001-request.http`):

```json
{"model": "Qwen/Qwen3-0.6B", "max_tokens": 200, "messages": [{"role": "user", "content": "SCRIPT:answer"}], "stop_sequences": ["</answer>", "\n\nHuman:"]}
```

The engine's script for this prompt is `<answer>2 plus 3 is 5</answer>\nThis text must never be generated.` (20 tokens). What it actually emitted before the frontend cancelled the stream (`dynamo/forwarded/*-0001.json`, `emitted_text`, `termination: "context_stopped"`, 13 tokens):

```text
<answer>2 plus 3 is 5</answer>

```

Dynamo response (`dynamo/0001-response.http`):

```json
{"id":"msg_...","type":"message","role":"assistant","content":[{"type":"text","text":"<answer>2 plus 3 is 5"}],"model":"Qwen/Qwen3-0.6B","stop_reason":"end_turn","stop_sequence":null,"usage":{...}}
```

The streamed variant (`dynamo/0006-response.http` to `0008`) ends with `{"type":"message_delta","delta":{"stop_reason":"end_turn"},...}`, with no `stop_sequence` key.

### Controls

| Rung | Request | Result | Runs |
|---|---|---|---|
| Provider direct (`anthropic-live/`) | same `stop_sequences`, prompt asks for the same text | text `<answer>2 plus 3 is 5`, `stop_reason: "stop_sequence"`, `stop_sequence: "</answer>"`, unary and stream | 8/8 |
| Same frontend, chat surface (`dynamo/0009` to `0011`) | `/v1/chat/completions`, `stop: ["</answer>", "\n\nHuman:"]`, `nvext.extra_fields: ["stop_reason"]` | `finish_reason: "stop"`, `nvext.stop_reason: "</answer>"` | 3/3 |
| Trigger removed (`dynamo/0012` to `0014`) | `/v1/messages`, no `stop_sequences` | full text through `generated.`, `end_turn` (correct) | 3/3 |
| Length stop at the delimiter boundary (`dynamo/0015` to `0020`) | bug request with `max_tokens: 10`, the exact token count of `<answer>2 plus 3 is 5` | engine emits 10 tokens and finishes with `length`, without the delimiter; Dynamo returns the same text with `stop_reason: "max_tokens"` (correct) | 6/6 (3 unary, 3 streamed) |
| Current release (`dynamo-release-1.5.0/`) | the full matrix above on `ai-dynamo==1.5.0`, with engine records | bug case `end_turn`, `stop_sequence: null`; chat, trigger-removed, and length controls as on the nightly | 8/8 bug, 3/3, 3/3, 6/6 |

What this rules out:

- **Provider or model**: the live API returns the same text and reports the delimiter.
- **Engine**: the engine only returns token ids. The stop is detected in the frontend, and the frontend knows the matched string, because the chat surface of the same process reports it.
- **Configuration and input**: removing only `stop_sequences` gives a correct natural `end_turn` with the full text.
- **A length stop mistaken for a delimiter stop**: when the budget ends exactly where `</answer>` would begin, the engine never emits the delimiter and Dynamo correctly says `max_tokens`. In the bug case the engine did emit `</answer>`, so the stop was the delimiter.
- **Version drift**: identical behavior on the release and on the nightly, and the source lines below are unchanged on `main` `d39d11af`.

The 1.5.0 run also shows the text ending in `</answer` (a partial stop string). That is the separate, already fixed defect [#14378](https://github.com/ai-dynamo/dynamo/pull/14378) (merged 2026-09-11, not in 1.5.0). It is absent on the nightly and is not part of this claim.

### Consumer boundary (`consumer/`)

`rig/consumer_tooluser.py` runs [`anthropics/anthropic-tools`](https://github.com/anthropics/anthropic-tools) at `795f706ddaabd2dc71f6c6d47ba087e7df03205a` unmodified, with that repository's pinned `anthropic==0.16.0`, against the Dynamo frontend via `ANTHROPIC_BASE_URL`. The tool is the package's calculator example (`perform_addition`). Every execution is appended to `consumer/tool-ledger.jsonl`. The engine's script is `<function_calls>...<invoke>...perform_addition...</invoke>\n</function_calls>` followed by trailing text. Its records (`consumer/forwarded/`) show that on all 20 first-turn calls it emitted 48 of 55 planned tokens, ending in `</function_calls>\n`, before the frontend cancelled the stream.

| Mode | Change | Outcome | Tool executions |
|---|---|---|---|
| `dynamo` | none, real Dynamo response | `ValueError: Hit maximum number of retries attempting to use tools.` | 0 of 5 runs |
| `counterfactual` | Dynamo response with only `stop_reason` and `stop_sequence` set to what the live API reports for a delimiter stop (`stop_sequence`, `"</function_calls>"`) | `"2 plus 3 is 5."` | 5 of 5 runs, once each |

The chain inside `ToolUser`: `messages_api_converters.py:43-46` substitutes `"\n\nHuman:"` when `stop_sequence` is null. `tool_user.py:77-80` re-appends `</function_calls>` only when the stop was that sequence. `tool_user.py:253` then requires `<function_calls>(.*)</function_calls>`, which can never match text whose closing tag the server stripped and did not report.

The live API could not serve as the consumer control here, because current Claude models do not emit the legacy `<function_calls>` markup (5/5 runs stopped after the preamble with `end_turn`). The counterfactual changes only the two fields under dispute, and the live API's behavior for those fields is established separately above.

## Root cause

`lib/llm/src/protocols/anthropic/types.rs` on `main` `d39d11af`, unary conversion:

```rust
stop_reason = choice.finish_reason.map(|fr| match fr {
    dynamo_protocols::types::FinishReason::Stop => AnthropicStopReason::EndTurn,   // :735
    ...
});
...
AnthropicMessageResponse { ..., stop_reason, stop_sequence: None, usage }            // :843
```

The streaming converter does the same at `stream_converter.rs:507` and `:877` (`FinishReason::Stop => AnthropicStopReason::EndTurn`), and builds both `message_delta` events with `stop_sequence: None` (`:702`, `:1050`). The `None` at `:428` is in `message_start`, where it is correct.

The information exists one layer down. `lib/llm/src/backend.rs:442-447` turns `StopTrigger::HiddenStopSequenceDetected(seq)` into `StopReason::String(seq)`, and the chat delta generator carries it (`protocols/openai/chat_completions/delta.rs:276`), which is how `nvext.stop_reason` reports `"</answer>"`. The Anthropic converters only see the OpenAI-shaped `finish_reason: "stop"` and never read the matched sequence. `dynamo-protocols` 6.1.0 defines `AnthropicStopReason::StopSequence` (`src/types/anthropic.rs:726`), but nothing in the frontend constructs it.

## Bug or not

- **Is the expected behavior really the spec?** Yes. The Messages API reference defines `stop_reason: "stop_sequence"` for "one of your provided custom `stop_sequences` was generated" and a `stop_sequence` field naming it. The live API does this 8/8 on the same request. Dynamo's own protocol crate defines the `StopSequence` variant, and its closed PR #8284 planned a test for exactly this. Peer servers implement it: vLLM's Anthropic adapter test asserts `stop_reason == "stop_sequence"` and `stop_sequence == "</tool>"` (`tests/entrypoints/anthropic/test_anthropic_messages_conversion.py:1584-1585`, `vllm-project/vllm` at `28c57456db92`), and TensorRT-LLM asserts `stop_sequence == "END"` (`tests/unittest/llmapi/apps/test_anthropic_adapter.py:776-777`, at `2334357ec178`). Nothing in Dynamo's docs, tests, or examples says the field is intentionally omitted.
- **Have maintainers already ruled on it?** No. No issue, review comment, or commit classifies a stop-sequence stop as `end_turn` on purpose. The only maintainer artifact found (#8284) expected the opposite.
- **Is the trigger supported usage?** Yes. `--enable-anthropic-api` is the documented setup, and `stop_sequences` is accepted and applied (`types.rs:133-137` maps it to `stop`). No unusual flag is needed.
- **Is a real boundary crossed?** This is not a disclosure claim. The crossed contract is the Messages API's stop reporting, and the measured consequence is an agent loop that cannot execute its tool.
- **What fix would a maintainer ship?** When the backend reports a user stop-sequence stop, map it to `stop_reason: "stop_sequence"` and put the matched string in `stop_sequence`, in both the unary and the streaming Anthropic converters, using the `StopReason::String` the frontend already computes.

Label: `bug`.

## Test

- Checker: `anthropic_stop_sequence_reported` in `crates/harness/src/checks.rs`. Invariant: when a generation ends because a requested stop sequence was produced, the Messages response says `stop_reason: "stop_sequence"` and names the sequence. The `generation` it reads is the emitted output, never a plan. A reported sequence must have been requested and be absent from the text. When the generation is known, it must begin exactly where the text ends, and no other requested sequence may complete first. Any other stop reason must leave `stop_sequence` null. A `max_tokens` stop must have used its whole budget when both counts are recorded, and is not treated as a missed delimiter, because a length stop can end exactly where a delimiter would begin. Any other stop reason is a violation when the returned text reaches a requested sequence in the emitted generation.
- Conformance, in `crates/harness/tests/conformance.rs`:
  - `dynamo_messages_stop_sequence_not_reported_violation` and `dynamo_release_messages_stop_sequence_not_reported_violation` assert `Violation` on every one of the 8 records in `capture-dynamo-bug.jsonl` (nightly) and `capture-dynamo-release-1.5.0-bug.jsonl` (1.5.0).
  - `dynamo_messages_length_stop_at_delimiter_boundary_is_conformant` asserts `Conformant` on the 12 boundary length stops from both versions.
  - `anthropic_live_stop_sequence_report_is_conformant` asserts `Conformant` on the live provider capture.
  - `dynamo_messages_without_stop_sequences_is_conformant` asserts `Conformant` on the trigger-removed capture from both versions.
- Unit tests for the checker cover the dropped report, a correct report, hosted-provider validation (unrequested, null, leaked, and invented sequences), a natural end, a length stop before the sequence, a length stop exactly at the delimiter boundary (with and without in-flight tokens after it), `max_tokens` with unused budget, the wrong delimiter named (`[END, STOP]` with `helloENDtailSTOP` reported as `STOP`), a sequence claimed after earlier truncation, a later sequence named when an earlier one completed, a non-null `stop_sequence` on `end_turn`, the 1.5.0 partial-delimiter shape, and malformed or empty captures.

## Reproduce

```bash
# 1. Frontend and deterministic engine (Docker, linux/arm64 or amd64)
docker run -d --name dynrig -p 18000:8000 -v "$PWD/transcripts/094/rig:/rig" python:3.12-slim sleep infinity
docker exec dynrig bash -c "apt-get update -qq && apt-get install -y -qq curl procps >/dev/null && \
  pip install -q --pre 'ai-dynamo==1.6.0.dev20261004' 'ai-dynamo-runtime==1.6.0.dev20261004' && \
  mkdir -p /rig/scripts /rig/logs /rig/captures && cp /rig/answer.txt /rig/xmltool*.txt /rig/scripts/ && \
  /rig/start.sh hermes qwen3"

# 2. Bug case
curl -s localhost:18000/v1/messages -H 'content-type: application/json' -H 'anthropic-version: 2023-06-01' \
  -d '{"model":"Qwen/Qwen3-0.6B","max_tokens":200,"stop_sequences":["</answer>","\n\nHuman:"],"messages":[{"role":"user","content":"SCRIPT:answer"}]}'
# -> "stop_reason":"end_turn","stop_sequence":null

# 3. Same frontend, chat surface: the matched string is known
curl -s localhost:18000/v1/chat/completions -H 'content-type: application/json' \
  -d '{"model":"Qwen/Qwen3-0.6B","max_tokens":200,"stop":["</answer>","\n\nHuman:"],"messages":[{"role":"user","content":"SCRIPT:answer"}],"nvext":{"extra_fields":["stop_reason"]}}'
# -> "nvext":{"stop_reason":"</answer>"}

# 4. Full matrix through a recording proxy, provider control, and consumer
python3 transcripts/094/rig/recproxy.py 18001 &            # REC_DIR selects the output directory
python3 transcripts/094/rig/dynamo_capture.py
ANTHROPIC_API_KEY=... python3 transcripts/094/rig/anthropic_control.py out 5 0
git clone https://github.com/anthropics/anthropic-tools /tmp/anthropic-tools   # 795f706
python3 -m venv v && v/bin/pip install anthropic==0.16.0 anthropic-bedrock==0.8.0 httpx==0.25.0 pydantic==2.4.2
ANTHROPIC_BASE_URL=http://localhost:18001 ANTHROPIC_API_KEY=dynamo-local v/bin/python transcripts/094/rig/consumer_tooluser.py dynamo
python3 transcripts/094/build_capture.py && cargo test --workspace

# 5. The same matrix on the 1.5.0 release, in a venv beside the nightly
docker exec dynrig bash -c "python -m venv --system-site-packages /opt/v150 && \
  /opt/v150/bin/pip install -q --no-deps 'ai-dynamo==1.5.0' 'ai-dynamo-runtime==1.5.0'"
docker exec -e PY=/opt/v150/bin/python dynrig /rig/start.sh hermes qwen3
```

`start.sh` starts the frontend with `--discovery-backend file --enable-anthropic-api` and one engine. The engine picks its completion by the last `SCRIPT:<name>` marker in the prompt, so a reviewer can change the generation by editing a text file. `CAPTURE_DIR` selects where it writes its records. It honors `max_tokens` from the forwarded stop conditions, as a real engine does.

## What was not verified

- No GPU model was run. The engine is deterministic and returns fixed token ids through Dynamo's public backend API. The claim concerns only how the frontend reports a stop that it detects itself, so the engine choice does not affect it. A vLLM, SGLang, or TRT-LLM worker was not run.
- "Emitted" means handed by the engine to the Dynamo runtime. The frontend's internal receipt of each chunk was not observed separately. The client responses and the chat surface's `nvext.stop_reason` show that the frontend saw the delimiter.
- The `ToolUser` success path is a counterfactual on the client side (two response fields changed), not a fixed Dynamo build. No patched frontend was built.
- `ToolUser` sends the transcript as a trailing assistant message (prefill), which Dynamo does not continue. The scripted engine selects completions by marker, so this did not affect these runs. With a real model the second turn would also depend on prefill handling. The first-turn failure measured here does not.
- The NYU LLM-CTF and OpenPipe ART consequences were traced in source, not run. Claude Code, Codex, and other harnesses were not tested against this defect.
- `anthropics/anthropic-tools` is archived. It is cited as the canonical, widely copied use of the field, not as a maintained product.
