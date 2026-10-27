# 110, LiteLLM `/v1/messages` with `stop_sequences` on GPT-5 now fails with `UnsupportedParamsError`: documented behavior, not a bug

**Verdict: false positive as a bug. Withdrawn by the author before review and
kept as an honest negative. Label `feature-request`. The bytes reproduce 66/66;
the bug claim does not hold. See "Bug or not" and "Mistakes not to repeat".**

## Claim (as first framed)

[#48534](https://github.com/BerriAI/litellm/pull/48534) (merged 2026-10-27, the
fix for [#37118](https://github.com/BerriAI/litellm/issues/37118)) sends every
`/v1/messages` request with a non-empty `stop_sequences` to Chat Completions
instead of the Responses API. GPT-5 family models list `stop` as unsupported there,
so a request that returned 200 before the change now returns 400
`litellm.UnsupportedParamsError` without reaching OpenAI.

- Upstream project: [BerriAI/litellm](https://github.com/BerriAI/litellm)
- Cited upstream issue or report: none for this effect; the change is #48534 for #37118
- Target type: open-source, run locally
- Tested commit: `main` `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` (2026-10-27,
  contains #48534 as `aa33b05`). Control `74ce6d1d66d22626fad72fbb9219d3cadf134b77`
  (the merge's parent). Latest release `v1.106.4` (2026-10-25) does not contain
  #48534, so no release is affected yet
- Client dialect and endpoint: Anthropic Messages, `POST /v1/messages`, plain and streaming
- Backend: live OpenAI API through a recording relay
- Models: `gpt-5.4-mini`, `gpt-5.5`; control `gpt-4.1-mini`
- Configuration: master key only, default `drop_params` (unset). Python 3.12,
  macOS arm64

## Gate 1: correctness (PASS for the bytes)

### Reproduction

```text
KAIRO_ENV=/path/to/.env transcripts/110/rig/reproduce.sh   # needs uv, git, python3, OPENAI_API_KEY
```

Wire matrix, [`transcripts/110/wire/results.json`](../../transcripts/110/wire/results.json),
`stop_sequences: ["STOPHERE"]`, plain and streaming, 3 runs each, so 6 cells per row:

| Model | `main` (#48534) | `pre` (`74ce6d1`) | `fix` (`fix.diff`) |
|---|---|---|---|
| gpt-5.4-mini | 400 `UnsupportedParamsError`, nothing forwarded, 6/6 | 200 via `/v1/responses`, `stop` dropped, text `ALPHA STOPHERE OMEGA`, 6/6 | same as `pre`, 6/6 |
| gpt-5.5 | 400 `UnsupportedParamsError`, nothing forwarded, 6/6 | 200, `stop` dropped, 6/6 | same as `pre`, 6/6 |
| gpt-4.1-mini | 200 via `/v1/chat/completions` with `stop`, text `ALPHA `, 6/6 | 200, `stop` dropped, full text, 6/6 | same as `main`, 6/6 |

Trigger removed (no `stop_sequences`) on `main`: gpt-5.4-mini and gpt-5.5 return
200 via `/v1/responses`, 12/12.

Direct OpenAI control, no gateway, [`direct/results.json`](../../transcripts/110/direct/results.json), 3 runs each:

| Request | gpt-5.4-mini | gpt-5.5 | gpt-4.1-mini |
|---|---|---|---|
| Chat Completions with `stop` | 400 `Unsupported parameter: 'stop' is not supported with this model.` | 400, same | 200, stops before `STOPHERE` |
| Responses with `stop` | 400 `Unknown parameter: 'stop'.` | 400, same | not run |
| Responses without `stop` | 200 | 200 | not run |

So OpenAI cannot honor a stop sequence on these models through either API. The
only ways to serve the request are to drop the constraint or to refuse it.

### False-positive checks

- [x] Tested the exact behavior on the commit that introduced it and its parent.
- [x] Pinned both commits; `main` unchanged in the touched files at the time of writing.
- [x] Ruled out bad configuration: default settings, valid Anthropic requests.
- [x] Model nondeterminism is irrelevant to the status codes; text checked only for the stop marker.
- [x] Live provider, not a mock.
- [x] Sanitized: credential and account headers redacted at capture time.

## Gate 2: usefulness (FAIL)

### Who it was supposed to hurt

- **Claude Code**: its auto-mode classifier sends `stop_sequences: ["</severity>"]`
  (seen live on 2.1.289). But LiteLLM turns `drop_params` on by default for
  `claude-cli/` and Codex user agents (`should_auto_drop_params_for_agentic_cli` in
  `litellm/proxy/litellm_pre_call_utils.py`), so the classifier call gets 200 on
  `main`. Not affected.
- **Other Anthropic-dialect clients** without `drop_params`: they get a 400 whose
  message names the remedy (`drop_params: true`).

### Consumer boundary: the evidence points the other way

LangChain classic ReAct agent on `ChatAnthropic`, which binds
`stop_sequences: ["\nObservation"]`, one tool that records an order,
[`agents/langchain-react/results.json`](../../transcripts/110/agents/langchain-react/results.json):

| Model and build | Outcome | Tool ran once with input `A-17` |
|---|---|---|
| gpt-5.4-mini, `main` | `AnthropicInvalidRequestError` 400 on the first call, tool never runs | 0/3, and 0 tool calls in all 3 |
| gpt-5.4-mini, `pre` (stop dropped) | completes | 0/3: every run passed the model's invented continuation into the tool (`"A-17It is done."`, `"A-17\nDone."`); 1/3 ran the tool twice |
| gpt-5.4-mini, `fix` (stop dropped) | completes | 0/3: same corruption; 2/3 ran the tool 2 or 3 times |
| gpt-4.1-mini, `main` (stop honored) | completes | 3/3 |

Without the stop sequence, GPT-5 writes its own `Observation:` and final answer
into the same turn, and the agent acts on that. The silent drop that `pre`
performed, and that the candidate fix restored, corrupts tool input and repeats
side effects. The 400 on `main` fails fast before any side effect.

### Bug or not

- **Spec**: LiteLLM's [Drop Unsupported Params](https://docs.litellm.ai/docs/completion/drop_params)
  page, checked 2026-10-27: "By default, LiteLLM raises an exception if you send a
  parameter to a model that doesn't support it." The 400 on `main` is that
  documented default. The old 200 was the inconsistency: a silent drop of a
  constraint the client asked for.
- **Maintainer ruling**: yes. The agentic-CLI auto-drop is a deliberate, scoped
  decision ("drop_params defaults to on for agentic CLIs ... An explicit drop_params
  from the caller or in the operator's `litellm_settings` always wins"). Everyone
  else gets the documented raise.
- **Kairo's own invariant agrees**: finding 032 froze `stop_sequence_forwarded`,
  "a client-supplied stop sequence MUST reach the upstream", because a silent drop
  changes where the model stops and the client cannot detect it. The candidate
  fix violates it.
- **Supported usage**: yes, default settings. But the remedy is the documented
  `drop_params: true`, which is "configure it", not a code fix.
- **Boundary crossed**: not a disclosure claim.
- **Fix a maintainer would ship**: none as a bug fix. A request to drop
  unsupported `stop_sequences` by default on `/v1/messages` would be a
  `feature-request`, and the consumer result above argues against it.
- **Label**: `feature-request`.

## Gate 3: upstream status (PASS)

- Date checked: 2026-10-27, against `main` `cfe0100e` and release `v1.106.4`.
- Searched issues and PRs: `stop_sequences`, `stop_sequences gpt-5`,
  `stop_sequences UnsupportedParamsError`, `v1/messages stop_sequences`,
  `does not support parameters stop gpt-5`, `48534`. Hits: #37118 (the original
  report, open, linked to #48534), #33075 (closed as stale 2026-10-19: Azure
  rejected a forwarded `stop_sequences`, and it names Claude Code's classifier),
  #47946, #46977, #40064, #10080 (o3 / o4-mini `stop`, closed 2025). None reports
  this effect.
- Docs: the `drop_params` page quoted above.
- Classification: `documented-behavior`.

## Mistakes not to repeat

1. **A 200 that became a 400 is not a bug by itself.** Before calling a new error
   a regression, find the project's documented error contract (here `drop_params`)
   and ask what the old 200 was really doing. Here it was silently dropping the
   client's constraint.
2. **Run the consumer before the matrix.** One LangChain run showed the drop
   corrupting tool input. The 66 wire cells were built first, and the consumer
   overturned them.
3. **Look for client-specific shields before naming a consumer.** LiteLLM
   auto-enables `drop_params` for Claude Code and Codex by user agent, so the most
   obvious victim was never affected. Replaying a captured body without its headers
   gave a 400 that the real client never sees.
4. **Check kairo's own frozen invariants against the proposed fix.** 032 already
   classifies a silent stop drop as a violation; the fix would have reintroduced it.
5. **Count what the consumer did exactly.** The first ledger wrote one line per
   call, the corrupted inputs contained newlines, and executions were overcounted.
   Use one JSON record per tool call.
6. **A string-search checker is fooled when the prompt echoes the needle.** This
   prompt contains `STOPHERE`, so `stop_sequence_forwarded` would pass on any body.
   The tests assert on the forwarded `stop` field instead.

## Frozen measurement

[`crates/harness/tests/conformance.rs`](../../crates/harness/tests/conformance.rs):

- `bug110_gpt5_stop_sequences_fail_fast_on_main_and_are_silently_dropped_before`
  re-derives all 66 wire cells from raw bytes: `main` refuses GPT-5 with nothing
  forwarded; `pre` and `fix` forward to `/v1/responses` with no `stop` and the
  marker appears in the text; `gpt-4.1-mini` on `main` and `fix` forwards
  `stop: ["STOPHERE"]` to `/v1/chat/completions` and the text stops before it.
- `bug110_direct_openai_rejects_stop_on_gpt5_on_both_apis` pins the 21 direct cells.
- `bug110_dropping_stop_corrupts_the_react_agent_tool_input` checks that every
  agent request carried `stop_sequences: ["\nObservation"]`, that `main` on GPT-5
  never ran the tool, that every stop-dropped run passed a corrupted input, and that
  `gpt-4.1-mini` ran it once with `A-17` 3/3.

## Limits and side notes

- Only `gpt-5.4-mini` and `gpt-5.5` were run. LiteLLM lists `stop` as unsupported
  for every GPT-5.x and GPT-6.x model it maps (checked with
  `get_supported_openai_params`), and as supported for `o3` and `o4-mini`.
- Azure was not run; its `/v1/messages` route already used Chat Completions before
  #48534.
- One ReAct agent was the consumer. A client that uses a stop sequence only to save
  tokens loses a working request without `drop_params`; that is the documented
  trade-off, and the error names the setting.
- Unrecorded side observation, one run each, not claimed: on the Chat Completions
  route, an Anthropic `top_k` is forwarded to OpenAI as is, so
  `stop_sequences` plus `top_k` on `gpt-4.1-mini` returns OpenAI's
  `Unrecognized request argument supplied: top_k` on `main` and 200 on `pre`.
