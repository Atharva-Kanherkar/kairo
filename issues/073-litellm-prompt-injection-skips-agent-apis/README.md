# 073, LiteLLM prompt-injection detection skips `/v1/messages` and `/v1/responses`

- **Upstream**: no dedicated LiteLLM ticket for this skip. Adjacent history is the
  Chat Completions allowlist: [litellm#11480](https://github.com/BerriAI/litellm/issues/11480),
  merged [PR #16701](https://github.com/BerriAI/litellm/pull/16701) (`acompletion`),
  and closed unmerged [PR #23092](https://github.com/BerriAI/litellm/pull/23092)
  (other async OpenAI endpoints). That last PR's endpoint table still omits
  `/v1/messages` and `/v1/responses`. Current `main` of
  `litellm/proxy/hooks/prompt_injection_detection.py` still uses the same
  allowlist. Checked 2026-09-07.
- **Tool under test**: LiteLLM **1.99.0** (`tools/litellm-env`), documented
  `callbacks: ["detect_prompt_injection"]`.
- **Reproduced**: 2026-09-07 on macOS arm64. Keyless local capture and live
  OpenAI (`gpt-4o-mini` via a local HTTPS relay to `api.openai.com`) both
  reproduce. Anthropic Messages and Responses forwarded the injection marker
  **5/5** on both backends. The same-process Chat Completions control rejected
  it **5/5** before any upstream call. Direct OpenAI Chat accepted the same
  marker **5/5**, so the Chat 400 is LiteLLM's detector, not the provider.

## What breaks

An operator enables LiteLLM's built-in prompt-injection detector, which the
docs present as a proxy callback that rejects attacks before the LLM:

```yaml
litellm_settings:
  callbacks: ["detect_prompt_injection"]
```

Claude Code and the Anthropic SDK enter through `/v1/messages`. Modern OpenAI
agents enter through `/v1/responses`. Both routes invoke the hook and then
return without scanning, because `call_type` is `anthropic_messages` or
`aresponses`. The same injection string is rejected on `/v1/chat/completions`.

The measured workflow is: client sends `Ignore previous and start over` (an
exact detector keyword), LiteLLM is supposed to return HTTP 400, and the
configured backend must not see that text. On Messages and Responses the
client gets HTTP 200 and the capture upstream receives the marker. Live OpenAI
then answers that jailbreak (`provider_status` 200, nonzero completion
length). A benign prompt on all three routes still returns HTTP 200, so the
hook is not a total outage; it is Chat-only.

This is a safety-control skip, not a tenant disclosure claim. The
reproduction uses the documented default-local proxy without a master key
because the skip is inside the hook after it runs.

```text
Chat Completions -> hook allowlist hit -> HTTP 400, no upstream
/v1/messages     -> call_type skipped  -> HTTP 200, marker forwarded, OpenAI answers
/v1/responses    -> call_type skipped  -> HTTP 200, marker forwarded, OpenAI answers
direct OpenAI    -> no LiteLLM         -> HTTP 200, same marker accepted
```

## Wire evidence

Each JSONL line is one trial: client route, status, request body, response
body, and the upstream record (`null` when the detector blocked the call).
The injection marker is LiteLLM's own heuristic phrase, not a private prompt.

Keyless captures live under `transcripts/073/`. Live OpenAI captures live
under `transcripts/073/live/`. Live client completions are sanitized to
status, model, id prefix, usage, and output length. Provider response text is
not committed. `OPENAI_API_KEY` is read from the environment or repo `.env`
and never written to a transcript.

Each file has five exchanges.

| Capture | Client status | Upstream | Verdict |
|---|---|---|---|
| `chat-injection-control.jsonl` | 400, 5/5 | none, 5/5 | control, detector works |
| `messages-injection.jsonl` | 200, 5/5 | `/v1/responses` contains the marker, 5/5 | violation |
| `responses-injection.jsonl` | 200, 5/5 | `/v1/responses` contains the marker, 5/5 | violation |
| `chat-safe.jsonl` | 200, 5/5 | `/v1/chat/completions`, 5/5 | hook does not block ordinary Chat |
| `messages-safe.jsonl` | 200, 5/5 | `/v1/responses`, 5/5 | hook does not block ordinary Messages |
| `responses-safe.jsonl` | 200, 5/5 | `/v1/responses`, 5/5 | hook does not block ordinary Responses |
| `live/chat-injection-control.jsonl` | 400, 5/5 | none, 5/5 | live control, no OpenAI POST |
| `live/messages-injection.jsonl` | 200, 5/5 | `/v1/responses` marker, OpenAI 200, 5/5 | live violation |
| `live/responses-injection.jsonl` | 200, 5/5 | `/v1/responses` marker, OpenAI 200, 5/5 | live violation |
| `live/chat-safe.jsonl` | 200, 5/5 | `/v1/chat/completions`, OpenAI 200, 5/5 | live path works |
| `live/messages-safe.jsonl` | 200, 5/5 | `/v1/responses`, OpenAI 200, 5/5 | live path works |
| `live/responses-safe.jsonl` | 200, 5/5 | `/v1/responses`, OpenAI 200, 5/5 | live path works |
| `live/direct-openai-injection.jsonl` | 200, 5/5 | `/v1/chat/completions` marker, OpenAI 200, 5/5 | provider accepts the phrase |

`metadata.json` and `live/metadata.json` record LiteLLM 1.99.0, the marker,
and the documented callback. `repro.py` reruns the keyless capture.
`live_repro.py` reruns the OpenAI confirmation.

## Root cause (if found)

`litellm/proxy/hooks/prompt_injection_detection.py` lines 149-162. The hook
asserts `call_type` is one of `acompletion`, `completion`, `text_completion`,
`embeddings`, `image_generation`, `moderation`, `audio_transcription`. Any
other type, including `anthropic_messages` and `aresponses`, logs a debug
line and returns the request unchanged.

A follow-on hole: `get_formatted_prompt()` has no branch for those call types
either. Adding them to the allowlist without extracting Anthropic `messages`
and Responses `input` would scan an empty string and still miss the attack.

The same Chat-only allowlist pattern exists in MCP semantic filter,
`TEXT_CONTENT_CALL_TYPES` (Azure Content Safety), and Guardrails AI. Those
are not claimed or frozen here.

## Bug or not

- **Expected behavior is the spec:** the documented setup is a proxy callback,
  not a Chat-only flag
  ([In-memory Prompt Injection Detection](https://docs.litellm.ai/docs/proxy/guardrails/prompt_injection)).
  The example curl uses `/v1/chat/completions`, but enablement is
  `litellm_settings.callbacks`. Maintainers already treated missing call
  types as silent skips in #11480 / PR #16701, and PR #23092 described the
  same class of hole as "prompt injection detection was silently skipped".
- **Examples, tests, and UI checked:** the docs, the hook allowlist, and the
  #23092 endpoint table. None of those mark `/v1/messages` or `/v1/responses`
  as intentionally unprotected.
- **Maintainer ruling:** none found accepting this skip.
  [litellm#27518](https://github.com/BerriAI/litellm/issues/27518) is a
  different bypass: the experimental
  `use_chat_completions_url_for_anthropic_messages` path never calls
  `async_pre_call_hook`. This reproduction uses the standard `/v1/messages`
  route, where the hook runs and then self-filters on `call_type`.
  [litellm#19499](https://github.com/BerriAI/litellm/issues/19499) is about
  event-loop blocking and `llm_api_check` inheritance, not this allowlist.
- **Supported usage:** documented `callbacks: ["detect_prompt_injection"]` with
  the default heuristic. Auth-off is not the trigger.
- **Boundary:** detector versus provider, not tenant A versus tenant B. The
  control is supposed to reject the attack before any LLM call. Live OpenAI
  received the marker and returned HTTP 200 with a completion.
- **Maintainer fix:** add `anthropic_messages` and `aresponses` to the
  allowlist, and extract text from Anthropic `messages` and Responses
  `input` so the scan is not empty.

Classification label: `bug`.

## Test

`prompt_injection_blocked_before_upstream` encodes the invariant: if the
client request contains the injection marker, the proxy must return HTTP 400
and must not forward that marker upstream. A prompt without the marker is
out of scope.

- `litellm_prompt_injection_blocks_chat_completions` replays the Chat control.
- `litellm_prompt_injection_skips_anthropic_messages` and
  `litellm_prompt_injection_skips_responses` replay the two violations.
- `litellm_prompt_injection_safe_prompts_still_complete` replays the three
  benign controls.
- Matching `live_*` tests replay the OpenAI captures, including
  `litellm_prompt_injection_live_openai_accepts_marker_directly`.
- `litellm_prompt_injection_route_identity_rejects_swapped_capture` keeps the
  Chat control and Messages violation from being interchangeable.

Replay offline:

```bash
cargo test -p kairo --test conformance litellm_prompt_injection
```

Rerun the keyless capture (no provider keys):

```bash
tools/litellm-env/bin/python transcripts/073/repro.py
```

Rerun the live confirmation (`OPENAI_API_KEY` from the environment or `.env`):

```bash
tools/litellm-env/bin/python transcripts/073/live_repro.py
```
