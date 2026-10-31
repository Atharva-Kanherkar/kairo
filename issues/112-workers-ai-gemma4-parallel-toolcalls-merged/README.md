# 112, Workers AI's `-external` backend for Gemma 4 merges parallel tool calls into one unparseable call

- **Upstream**: Cloudflare Workers AI, hosted and closed source. No public issue,
  pull request, changelog entry, or community thread reports this on 2026-10-31
  (searches below). The nearest public report, [cloudflare/ai#561](https://github.com/cloudflare/ai/issues/561)
  (Gemma 4 `tool_choice` and non-stream token loss), is a different defect.
  Classification: `novel`. Not yet reported to Cloudflare.
- **Tool under test**: the hosted Workers AI model `@cf/google/gemma-4-26b-a4b-it`,
  reached through the documented Workers `AI` binding (`env.AI.run`) from a local
  `wrangler dev` 4.139.0 Worker on a free-plan account. Workers AI exposes no build
  or release id, so the finding is against the deployment observed between
  2026-10-31T05:56:13Z and 05:59:27Z (committed matrix), with an earlier
  exploratory window on the same day between about 05:37Z and 05:50Z that
  matched. Edge colo in `cf-ray`: NRT.
- **Reproduced**: 2026-10-31. Same model id, same input, same account; the only
  thing that varies between good and bad runs is the backend Workers AI picks,
  which the response names in its `model` field.

## Claim

Workers AI serves `@cf/google/gemma-4-26b-a4b-it` from two backends, and the
response says which one answered: `"model": "@cf/google/gemma-4-26b-a4b-it"` or
`"model": "@cf/google/gemma-4-26b-a4b-it-external"`. When the model makes two
tool calls in one turn and the `-external` backend serves it:

- **non-streaming**, the response carries **one** `tool_calls` entry whose
  `arguments` are the two calls' objects concatenated,
  `{"city": "Paris"}{"city": "Tokyo"}`;
- **streaming**, the two calls arrive with different ids but **both on
  `index: 0`**, so every index-keyed accumulator rebuilds the same concatenated
  string.

No client can parse that string. The second call is gone and the first never
runs. The native backend, the same weights served by another host, and a
single tool call on either backend are all correct.

## What breaks

Anyone running an agent on Workers AI's Gemma 4 with more than one tool. Parallel
tool calls are the documented default: the model page lists function calling and
`parallel_tool_calls` "default: true", and the Workers AI changelog recommends
Gemma 4 as a migration target. An agent that asks for two lookups at once (two
files, two cities, two searches) gets back one call the SDK rejects.

Measured through Cloudflare's own SDK path, an AI SDK 7 `generateText` agent on
`createAI` from `agents/models/ai-sdk` 0.32.0 with a real executing tool and a
5-step limit:

| First step served by | Runs | Result |
|---|---|---|
| `-external` | 5 | 5/5 burned steps on `AI_InvalidToolInputError: ... JSON parsing failed: Text: {"city": "Paris"}{"city": "Tokyo"}`; 2/5 hit the step limit with an **empty final answer**; 1/5 never ran the Tokyo lookup; steps used 3, 3, 4, 5, 5 |
| native | 1 | answered in 2 steps, both tools ran once |

The agent recovers only when the model gives up on parallel calls and retries
one at a time, or when a later step happens to land on the native backend. In
run 4 the model resent the same merged call four times and ended with no answer.

## Wire evidence

All bytes are under [`transcripts/112/`](../../transcripts/112/). Each run has the
exact `binding.run` request (`request.json`) and the exact HTTP response Workers
AI returned to the binding (`response.http`, status, headers, body).

**`-external`, non-streaming** (`cells/plain-parallel-nonstream/run-01`):

```json
"model": "@cf/google/gemma-4-26b-a4b-it-external",
"tool_calls": [{"id": "call_fee5bbc604614603bfb845b2", "type": "function",
  "function": {"name": "get_weather",
               "arguments": "{\"city\": \"Paris\"}{\"city\": \"Tokyo\"}"}}],
"finish_reason": "tool_calls"
```

**native, non-streaming, same request** (`cells/plain-parallel-nonstream/run-09`):

```json
"model": "@cf/google/gemma-4-26b-a4b-it",
"tool_calls": [
  {"id": "chatcmpl-tool-875f655e8e42d1b4", "function": {"name": "get_weather", "arguments": "{\"city\": \"Paris\"}"}},
  {"id": "chatcmpl-tool-bdc484435f52d984", "function": {"name": "get_weather", "arguments": "{\"city\": \"Tokyo\"}"}}]
```

**`-external`, streaming** (`cells/plain-parallel-stream/run-03`), the
`tool_calls` deltas in order:

```text
{"id": "call_339490ce7ac34c97a8a49bb9", "index": 0, "function": {"name": "get_weather", "arguments": ""}}
{"id": null,                            "index": 0, "function": {"name": null, "arguments": "{\"city\": \"Paris\"}"}}
{"id": "call_47e714b63f2b4e569db96902", "index": 0, "function": {"name": "get_weather", "arguments": ""}}
{"id": null,                            "index": 0, "function": {"name": null, "arguments": "{\"city\": \"Tokyo\"}"}}
```

The native backend (`cells/plain-parallel-stream/run-01`) and OpenRouter serving
the same weights (`cells/direct-openrouter-parallel-stream/run-01`) put the
second call on `index: 1`.

### Results

Every cell, from [`transcripts/112/matrix.md`](../../transcripts/112/matrix.md):

| Cell | Served by | Result |
|---|---|---|
| Plain binding, two calls, non-streaming | `-external` | broken 9/9 |
| Plain binding, two calls, non-streaming | native | ok 1/1 |
| Plain binding, two calls, streaming | `-external` | broken 8/8 |
| Plain binding, two calls, streaming | native | ok 2/2 |
| Trigger removed: one call, non-streaming | `-external` | ok 5/5 |
| Trigger removed: one call, streaming | `-external` / native | ok 4/4 / ok 1/1 |
| OpenRouter, same weights, non-streaming | PrimeIntellect | ok 5/5 |
| OpenRouter, same weights, streaming | PrimeIntellect | ok 5/5 |
| `createAI` agent through AI Gateway | first step `-external` / native | see the table above |

Totals: `-external` with two calls is broken 17/17; the native backend with the
same input is correct 3/3; a single call is correct 10/10 across both backends;
the same model on OpenRouter is correct 10/10.

An exploratory pass earlier the same day, not committed, matched: 15/15 broken on
`-external` and 5/5 correct on native through the plain binding, and 4/4 plus 5/5
broken on `-external` through `createAI`. The same exploratory pass ran the
two-call prompt once streamed and once not on all 22 Workers AI models flagged
for function calling: 15 others returned two separate calls in both modes, 4
made fewer than two calls (`granite-4.0-h-micro` made one, with double-encoded
arguments, a separate issue), 2 were not callable on this account, and only
Gemma 4 merged.

### Control ladder

| Rung | Result | Evidence |
|---|---|---|
| Direct incumbent provider | Google's Gemini API lists `gemma-4-26b-a4b-it` but returned 402 (prepaid credits depleted on both test keys), so it could not run. Substitute: the same weights on OpenRouter (provider PrimeIntellect), same prompt and tool, separate calls 10/10 | `cells/direct-openrouter-*` |
| Plain, bypass, or non-routing path | `env.AI.run` with no gateway and no SDK: `-external` broken 17/17, native correct 3/3. There is no documented way to pin a backend, so the native rows are the runs Workers AI happened to route there | `cells/plain-parallel-*` |
| Routed or transformation path | `createAI` (`agents/models/ai-sdk`) through the account's `default` AI Gateway: the same merge reaches the agent in every step that `-external` served | `cells/consumer-createai-agent` |
| Trigger removed | One tool call per turn: correct 10/10, 9 of them on `-external` | `cells/trigger-removed-*` |

The REST endpoints (`/ai/run`, `/ai/v1/chat/completions`) were not exercised: no
API token was available, and the binding is the documented Workers entry point.

### False-positive checks

- **Model nondeterminism.** The verdict is structural, not textual: either the
  calls are separable or they are not. The model chose the same two calls in
  every run on both backends; only their framing differed, and it differed by
  backend 20/20 with no exceptions.
- **Client or SDK.** The plain cells call the binding directly and parse the
  bytes in Python; no AI SDK, adapter, or gateway is involved. The SDK cell only
  shows the consequence.
- **Configuration or input.** Same model id, inputs, account, and binding call in
  every cell. `temperature: 0`, `max_tokens: 400`, one function tool,
  default `parallel_tool_calls`.
- **Mock or harness.** No mock anywhere; every call reached the hosted service
  (`cf-ai-req-id` and `cf-ai-neurons` headers in each `response.http`).
- **Transient incident.** Two separate windows the same day agree.

## Root cause

Not observable from outside. The isolated layer is the Workers AI serving path
behind the `-external` model echo: the bytes are already merged in the response
the binding returns, before any client code runs. Both backends answer the same
model id; the native one emits vLLM-style `chatcmpl-tool-*` ids and correct
indexes, the external one emits `call_*` ids and reuses `index: 0`. The
`-external` suffix, and the quirks table in `agents/models/ai-sdk`
(`core/chat-completions/quirks.ts`, "echoes `@cf/google/gemma-4-26b-a4b-it-external`"),
suggest a second, possibly partner-run, serving stack; that is inference, and
nothing here shows which component concatenates the calls. The model page calls
the model "Cloudflare-hosted".

## Consumer boundary

`cells/consumer-createai-agent/run-NN/agent.json` records each agent run:
every step's tool calls, the `invalid` flag, the SDK's tool error text, the
tools that actually executed, the final text, and every underlying `binding.run`
call with its raw body. The AI SDK marks the merged call invalid with
`AI_InvalidToolInputError` / `AI_JSONParseError` ("Unexpected non-whitespace
character after JSON at position 17") and sends that error back to the model as
the tool result.

Measured: the step counts, empty answers, and missing executions in the table
above. Inferred, not run: openai-python and openai-node accumulate streamed tool
calls by `index` the same way, so a stream from `-external` would give them the
same merged call; and a model that cannot fall back to sequential calls would
loop until the step limit every time the first step lands on `-external`.

## Bug or not

- **Expected behavior is the spec.** The Workers AI response format is OpenAI
  Chat Completions, where each tool call is its own `tool_calls` entry and each
  streamed call has its own `index`. The Gemma 4 model page documents function
  calling and `parallel_tool_calls` "default: true", and Workers AI's own native
  backend for the same model id emits exactly that shape.
- **No maintainer ruling.** Nothing in docs, changelog, or the public trackers
  says Gemma 4 parallel calls are unsupported or merged.
- **Supported usage.** The documented binding call with default settings, a
  plain function tool, and an ordinary two-lookup prompt.
- **Boundary crossed.** Not a disclosure claim.
- **Fix a maintainer would ship.** Make the Gemma 4 `-external` serving path
  return each parallel call as its own `tool_calls` entry with its own streaming
  `index`, as the native backend already does.
- **Label: `bug`.**

## Upstream status

- **Date checked**: 2026-10-31.
- **Deployment checked**: the hosted Workers AI deployment in the windows above.
- **Searched** issues and pull requests in `cloudflare/ai`, `cloudflare/agents`,
  `cloudflare/cloudflare-docs`, and `cloudflare/workers-sdk` for `gemma-4
  parallel`, `gemma-4 tool_calls index`, `gemma-4-26b-a4b-it tool`, `gemma
  external`, `parallel tool calls merged`, `tool_calls index 0`, `arguments
  concatenated`, `parallel_tool_calls workers ai`; GitHub-wide for
  `gemma-4-26b-a4b-it-external`, `gemma-4-26b-a4b-it parallel tool`, `workers ai
  gemma 4 tool calls`. No match. The only GitHub hits naming the external backend
  are unrelated CI logs; `cloudflare/ai` PR #663 matched `tool_calls index 0` but
  is a client-side duplication in the deprecated `workers-ai-provider`.
- **Docs and changelog**: the Gemma 4 model page and the Workers AI changelog
  (2026-04-04 launch post) were read on 2026-10-31; neither mentions a parallel
  call limitation. The Cloudflare community search endpoint returned a non-JSON
  challenge page to the script and was not searched.
- **Classification**: `novel`. Workers AI bugs are filed publicly in
  `cloudflare/ai` (for example #561), so that is the natural place to report it.

## Test

Two checkers in `crates/harness/src/checks.rs`, run over every recorded run in
`crates/harness/tests/conformance.rs`:

- `openai_stream_toolcall_index_per_call`: in a Chat Completions stream, a
  second tool-call id never opens on an index another id already holds.
- `openai_toolcall_arguments_single_object`: in a non-streaming response, every
  `tool_calls[].function.arguments` decodes to exactly one JSON object.

The conformance tests group each cell's runs by the `model` the response names.
`-external` runs with two calls must violate, native runs, single-call runs, and
the OpenRouter control must conform, and each group must be non-empty, so a
re-recording that happens to miss a backend fails loudly instead of passing
vacuously. The checkers state the invariant any OpenAI-compatible server must
keep, not anything specific to Workers AI.

## Reproduce

```sh
cd transcripts/112/rig && npm ci
npx wrangler login               # account with Workers AI access
npx wrangler dev --port 8799     # leave running
# second shell, from transcripts/112:
OPENROUTER_API_KEY=... python3 reproduce.py   # writes cells/ and matrix.md
```

Backend selection is Workers AI's choice per request, so a rerun's split between
`-external` and native varies; the per-backend verdicts do not.
