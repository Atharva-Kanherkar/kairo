# 099, Dynamo's `hermes` tool parser ends a call at a `</tool_call>` inside a JSON string argument, so a well-formed Qwen3 tool call is dropped and the agent turn ends as text

- **Upstream**: [ai-dynamo/dynamo](https://github.com/ai-dynamo/dynamo), parser
  code in [ai-dynamo/frontend-crates](https://github.com/ai-dynamo/frontend-crates)
  (crate `dynamo-parsers`). No issue or pull request covers the Hermes JSON case.
  The same defect class in other parsers is already filed as a bug by NVIDIA
  contributors: [frontend-crates#410](https://github.com/ai-dynamo/frontend-crates/issues/410)
  (open, 2026-10-14, v2 `qwen3` parser cuts a value at an embedded
  `</parameter>`) and [frontend-crates#315](https://github.com/ai-dynamo/frontend-crates/issues/315)
  (open, DSML literal). See "Upstream status".
- **Tool under test**: the real Dynamo frontend (`python -m dynamo.frontend`)
  from PyPI `ai-dynamo` 1.6.0.dev20261014 (nightly, tracks `main`) and release
  `ai-dynamo` 1.5.0. Dynamo `main` at `094aaca9` (2026-10-15) pins
  `dynamo-parsers` `=10.0.2`. Worker registered with `--tool-call-parser hermes`,
  the parser Dynamo documents for Qwen2.5, QwQ, Qwen3-Instruct, Qwen3-Think and
  NousHermes. Routes `POST /v1/chat/completions` and `POST /v1/messages`, unary
  and streamed.
- **Engine**: a scripted engine on Dynamo's public `LLMEngine` backend API that
  emits, token for token through the Qwen3 tokenizer, completions recorded from
  the real model `qwen/qwen3-235b-a22b-2507`. The claim concerns what the
  frontend does with engine output, so a deterministic engine replaying real
  model bytes is the right upstream; a 235B model cannot be served on the test
  host. Every cell records what the engine actually emitted.
- **Consumers**: `openai` 3.31.0 and `anthropic` 1.14.0 in a minimal
  `write_file` agent loop.
- **Reproduced**: 2026-10-15, Docker `python:3.12-slim` on macOS arm64.
- **Label**: `bug`.

## What breaks

A coding agent backed by a Qwen3 model served through Dynamo asks the model to
write or rewrite a file whose content contains the text `</tool_call>`. That is
any test fixture, prompt template, chat template, parser source, or doc page in
a codebase that works with Hermes or Qwen tool calling. Dynamo's own `main` has
22 tracked files containing that string.

The model writes a correct call. The argument is a JSON string, and inside it
the closer is just text:

```text
<tool_call>
{"name": "write_file", "arguments": {"path": "tests/fixtures/single_call.txt", "content": "<tool_call>\n{\"name\": \"get_weather\", \"arguments\": {\"city\": \"Berlin\", \"unit\": \"celsius\"}}\n</tool_call>"}}
</tool_call>
```

Dynamo's `hermes` parser ends the call at the first `</tool_call>` it sees,
which is the one inside the string. The cut payload is not valid JSON, so the
call is discarded, and the client receives:

```json
{"choices":[{"index":0,"message":{"content":"\"}}\n</tool_call>","role":"assistant"},"finish_reason":"stop"}]}
```

No tool call, `finish_reason: "stop"` (Anthropic: `stop_reason: "end_turn"`),
and a fragment of tool-call markup as the visible answer. An agent loop treats
this as the model finishing its turn. The file is never written, and nothing in
the response says a tool call existed. This also breaks the parser's own stated
rule that `hermes` "must never surface tool-call markup to the user"
(`dynamo-parsers` 10.0.2, `src/tool_calling/config.rs:77-83`).

## Wire evidence

Real model output, 20 runs, `transcripts/099/realmodel/` (raw request with the
key redacted inline, raw response, extracted completion):

| Prompt | Well-formed call with `</tool_call>` inside an argument | Malformed by the model | No call |
|---|---|---|---|
| `verbatim`: create a fixture holding a sample Hermes call | 6/10 | 2/10 | 2/10 |
| `edit`: read an existing fixture, rewrite it with one value changed | 6/10 | 2/10 | 2/10 |

Every well-formed call in the 20 runs, 12 of 12, carries the literal closer
inside an argument. The model does not escape it, because JSON does not require
escaping `/`. The two "no call" rows per prompt came from one provider (Venice)
returning nothing usable, and the malformed rows are invalid JSON the model
wrote; neither is counted against Dynamo.

Through the real frontend, `transcripts/099/capture/`, one exchange per cell,
each with the exact socket bytes and the engine's own emitted-token record:

| Build | Cases | Trigger cells, call lost | Control cells, call intact |
|---|---|---|---|
| nightly, 1 token per chunk | `hcl_real`, `hcl_edit` | 40/40 | 20/20 |
| nightly, 5 tokens per chunk | `hcl_real`, `hcl_edit` | 40/40 | 20/20 |
| release 1.5.0, 1 token per chunk | `hcl_real`, `hcl_edit` | 24/24 | 12/12 |
| nightly, the 12 real completions replayed verbatim | `rc_*` | 48/48 | 48/48 |

Each row covers OpenAI and Anthropic, unary and streamed, equally.
`hcl_real` is byte-identical to recorded completions `verbatim-02`, `-04`,
`-08` and `-10`. `hcl_edit` is an `edit`-prompt completion from an earlier probe
run whose raw response was not kept; the `rc_*` row replays only completions
with retained raw bytes. In every
trigger cell the client sees 0 tool calls, `stop` or `end_turn`, and the text
`"}}\n</tool_call>` (or `\n"}}\n</tool_call>`). HTTP status is 200 throughout.

Example pair, `capture/nightly-realmodel-tpc1/rc_edit_01-openai-unary-01/`:
the engine emitted the call above (`engine-emitted.json`), and
`client-response.http` carries the text-only response above. The control
`rc_edit_01_ctrl-openai-unary-01/` differs only in `<\/tool_call>` inside the
string, which decodes to the same argument, and returns:

```json
{"message":{"content":null,"tool_calls":[{"type":"function","function":{"name":"write_file","arguments":"{\"path\": \"tests/fixtures/single_call.txt\", \"content\": \"<tool_call>\\n{\\\"name\\\": \\\"get_weather\\\", \\\"arguments\\\": {\\\"city\\\": \\\"Berlin\\\", \\\"unit\\\": \\\"celsius\\\"}}\\n<\\/tool_call>\"}"}}]},"finish_reason":"tool_calls"}
```

**Controls**

- Trigger removed: same completion, same tokenizer, same request, the closer
  inside the string escaped as `<\/tool_call>`. Same JSON value. 100/100
  control cells keep the call with identical arguments.
- Chunking: 1 and 5 tokens per engine chunk give identical results, so this is
  not a chunk-boundary race.
- Version: release 1.5.0 behaves the same as the nightly, so this is long
  standing, not a regression.
- Library: `transcripts/099/libprobe/` calls `dynamo-parsers` 10.0.2 directly
  on the recorded completions. `detect_and_parse_tool_call` returns 0 calls and
  empty text; `detect_and_parse_tool_call_with_recovery`, which the
  non-streaming aggregator uses, returns one call whose `content` is silently
  cut at the embedded closer. The escaped control returns the full value from
  both. This attributes the defect to the parser crate Dynamo ships, not to the
  harness or the HTTP layer.

## Consumer boundary

`transcripts/099/consumer/agent_loop.jsonl`: a minimal agent that runs every
`write_file` call it receives, 5 runs per SDK mode:

| Completion | OpenAI unary | Anthropic unary | Anthropic stream |
|---|---|---|---|
| literal closer in argument | 0 tool runs, file not written, 5/5 | 0/5 written | 0/5 written |
| escaped control | file written byte-exact, 5/5 | 5/5 | 5/5 |

Measured: the turn ends, the file is never written, and the user is shown
`"}}\n</tool_call>`. Inferred, not measured: an agent that checks its own work
will retry and hit the same wall every time, because the model's correct output
is the trigger.

## Root cause

`dynamo-parsers` 10.0.2, `src/tool_calling/json/mod.rs:57`, in
`find_tool_call_end_position_json`, which the streaming jail calls
(`src/tool_calling/jail/mod.rs:1952`) for `hermes`, `nemotron_deci` and
`qwen25`:

```rust
let Some(first_end) = chunk.find(end_token.as_str()) else {
```

The jail splits at the first `</tool_call>` byte match, without regard to JSON
string boundaries. The prefix then fails to parse, and because `hermes` sets
`discard_unparseable_wrapper: true` (`config.rs:571`), the wrapper is dropped
and only the tail after the split is released as content. The non-streaming
extractor has the same first-match search at
`src/tool_calling/json/base_json_parser.rs:48`, which is why the library
recovery path truncates instead.

`qwen25` and `nemotron_deci` share the code path; only `hermes` was run.

## Bug or not

- **Expected behavior is the spec**: the Hermes format wraps a JSON object in
  `<tool_call>` tags, and the Qwen tool prompt asks for exactly that. A JSON
  string may contain `</tool_call>` unescaped. Dynamo's own parser treats the
  payload as JSON everywhere else, and the escaped control proves the frontend
  passes the same value through when its bytes differ by one backslash.
- **Maintainer ruling**: none for this case. `discard_unparseable_wrapper` is a
  deliberate ruling for payloads that are malformed; this payload is valid, and
  the parser makes it malformed by cutting it. Maintainers file the same class
  as bugs for other formats (#410 by an NVIDIA contributor from their own
  `tool_calling_probe.py` verifier, which has delimiter-in-string cases for
  Qwen XML, GLM, Gemma, Harmony, MiniMax and DSML, but none for Hermes JSON).
- **Supported usage**: `--tool-call-parser hermes` with a Qwen3 instruct model
  is the documented setup (`docs/fern/pages/use-cases/tool-calling-and-reasoning/tool-call-parsing.mdx`),
  default settings, no unusual flags.
- **Boundary crossed**: not a disclosure claim. The broken contract is tool-call
  delivery: a valid call is never delivered.
- **Fix a maintainer would ship**: find the end of a Hermes payload by scanning
  the JSON value (string-aware) and then expecting `</tool_call>`, falling back
  to the first-match search only when the payload is not valid JSON.

Unlike #410's XML format, Hermes JSON has unambiguous string boundaries, so the
fix is local and does not need a format change.

## Upstream status

- **Date checked**: 2026-10-15.
- **Checked against**: Dynamo `main` `094aaca9`, nightly 1.6.0.dev20261014,
  release 1.5.0, `dynamo-parsers` 10.0.2 (pinned by `main`; crates.io latest
  `dynamo-parsers-v2` is 0.7.22).
- **Searches** in `ai-dynamo/dynamo` and `ai-dynamo/frontend-crates`, issues and
  pull requests: `"</tool_call>" inside arguments`, `hermes`, `delimiter`,
  `end token`, `tool_call_end`, `first occurrence`, `inside string`,
  `tool call truncated </tool_call>`, `qwen tool_call literal closing tag`. Also
  the 40 most recent `frontend-crates` issues by date, Dynamo's tool-calling
  docs, and the `ibhosale/e2e-verifier` branch's probe cases.
- **Matches**: same class, other parsers: frontend-crates
  [#410](https://github.com/ai-dynamo/frontend-crates/issues/410) and
  [#315](https://github.com/ai-dynamo/frontend-crates/issues/315), both open.
  Same class in the reference engines: vLLM
  [#44060](https://github.com/vllm-project/vllm/issues/44060) (open, MiniMax
  `</parameter>`), and SGLang
  [#42140](https://github.com/sgl-project/sglang/issues/42140) (open, `<think>`
  stripped from arguments). vLLM's and SGLang's Hermes and Qwen2.5 parsers use
  the same first-match search (read from source, not run).
- **Classification**: `novel` for the Hermes JSON parser in Dynamo.

## Test

Checker `engine_tool_calls_reach_client` in `crates/harness/src/checks.rs`.
Invariant: every well-formed tool call the engine emitted reaches the client as
a tool call with the same name and the same arguments, compared as JSON values
so equivalent escapes and key order do not matter. It reads OpenAI and
Anthropic responses, unary and streamed, including chunked framing, and fails
when the engine text has no well-formed call, so it cannot pass vacuously.

Conformance tests in `crates/harness/tests/conformance.rs` assert all 152
trigger cells violate and all 100 control cells conform, per build, and that
all 12 well-formed real completions carry the literal closer. Unit tests cover
the string-aware scan, escape equivalence, truncated arguments, a lost call,
the non-vacuous rule, and a chunked Anthropic stream.

## Reproduce

```text
# Real-model leg (needs OPENROUTER_API_KEY in a dotenv file)
python3 transcripts/099/realmodel/capture_real.py <dotenv> out 10 verbatim
python3 transcripts/099/realmodel/capture_real.py <dotenv> out 10 edit

# Frontend leg, inside a python:3.12 container with this repo's rig at /rig
pip install --pre "ai-dynamo[runtime]==1.6.0.dev20261014" "ai-dynamo-runtime==1.6.0.dev20261014" transformers openai anthropic
cp transcripts/099/rig/scripts/*.txt /rig/scripts/
TPC=1 /rig/start.sh hermes none
python3 /rig/capture.py /rig/out 5 hcl_real hcl_ctrl hcl_edit
python3 /rig/agent_loop.py 5

cargo test --workspace -- 099 engine_calls
```

## Limitations

- The 235B model was not served by Dynamo. Its completions were recorded from
  OpenRouter's raw completion endpoint and replayed token for token through the
  Qwen3 tokenizer by a backend on Dynamo's public engine API. The parser sees
  the same text either way; a real engine could differ only in token
  boundaries, and chunking 1 and 5 tokens per chunk made no difference.
- How often real agent traffic contains this trigger was not measured. The
  trigger needs an argument that contains `</tool_call>`, which is narrow but
  deterministic, and common in exactly the repositories that build on these
  models.
- `qwen25` and `nemotron_deci` share the code but were not run. The v2 parser
  crate was not tested separately; the frontend's default path was.
