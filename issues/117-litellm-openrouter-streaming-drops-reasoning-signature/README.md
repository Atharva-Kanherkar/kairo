# 117, LiteLLM streaming silently drops OpenRouter reasoning_details chunks, corrupting thinking signatures and causing next-turn replay rejection

- **Upstream**: [BerriAI/litellm](https://github.com/BerriAI/litellm). Reported in open issue
  [#48401](https://github.com/BerriAI/litellm/issues/48401) (filed 2026-10-26), addressed by open PRs
  [#48412](https://github.com/BerriAI/litellm/pull/48412) (stream wrapper and serializer fix) and
  [#48462](https://github.com/BerriAI/litellm/pull/48462) (stream chunk builder fix).
  Classified `duplicate-open`.
- **Tool under test**: LiteLLM proxy `v1.106.6` / `1.108.0-dev` at commit
  `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` (2026-10-31), with master key enabled.
- **Provider**: Deterministic local mock OpenRouter completions server on `127.0.0.1:19996`
  implementing OpenRouter SSE streaming with Anthropic reasoning details and signature chunks.
- **Clients**: Standard streaming clients, agent loops, `httpx` 0.28.1.
- **Reproduced**: 2026-11-01, macOS arm64, Python 3.12.13. 5 of 5 runs per case across
  4 differential conditions (20 requests total).
- **Label**: `bug`

## Claim

When streaming completions from OpenRouter (`openrouter/*` models such as Claude 3.7 / Sonnet 4.5,
OpenAI o1/o3, or Gemini 2.5 Flash with thinking), LiteLLM silently drops SSE stream chunks that
carry only `delta.reasoning_details` (such as the closing cryptographic thought signature
`{"type": "reasoning.text", "signature": "..."}` or encrypted reasoning blobs). Because the
signature chunk is discarded by both the streaming chunk filter and the proxy SSE serializer,
clients reconstruct an assistant message containing thinking text but missing the required thought
signature. On the subsequent turn, replaying this corrupted history causes the provider to reject
the request with `400 Bad Request: Invalid thought signature`, permanently breaking multi-turn
reasoning agent sessions.

## What breaks

A developer or autonomous agent loop (such as Claude Code, Cursor, Windsurf, or OpenCode) connects
to an OpenRouter reasoning model through LiteLLM:

1. The client sends a streaming prompt to solve a reasoning problem or execute a multi-step tool task.
2. The model streams reasoning text deltas followed by a closing chunk that contains only the
   cryptographic thought signature in `delta.reasoning_details`.
3. In `litellm/litellm_core_utils/streaming_handler.py:971`, `CustomStreamWrapper.is_chunk_non_empty`
   evaluates whether the chunk contains content. Because `reasoning_details` is not checked and the chunk
   has no `content` or `reasoning_content` string, `is_chunk_non_empty` returns `False` and drops the chunk.
4. Furthermore, in `litellm/proxy/proxy_server.py:10086`, `_fast_serialize_simple_model_response_stream`
   omits `reasoning_details` from `unsupported_delta_fields` and only serializes `role` and `content`,
   stripping `reasoning_details` if reached.
5. The client reassembles the assistant response using `litellm.stream_chunk_builder` or standard client-side
   SSE chunk accumulators. The resulting assistant message retains thinking text but lacks the thought signature.
6. On the next turn (e.g. reporting tool output or continuing conversation), the agent submits the message
   history back to the provider.
7. Anthropic / OpenRouter rejects the turn with `HTTP 400 Bad Request: Invalid thought signature: thinking blocks in assistant message history must include a valid cryptographic signature`.
8. The agent session halts immediately.

## Bug or not

Before classifying this finding as a bug, the five usefulness questions from `AGENTS.md` are evaluated:

- **Is the expected behavior really the spec?** Yes. OpenRouter documentation explicitly mandates:
  "Preserving reasoning blocks is useful specifically for tool calling. When streaming, the final chunk
  contains the cryptographic signature that authenticates the reasoning block." Dropping signature-only
  chunks corrupts the reasoning protocol.
- **Have maintainers already ruled on it?** Yes. Upstream issue [#48401](https://github.com/BerriAI/litellm/issues/48401)
  is acknowledged as a bug, and two pull requests ([#48412](https://github.com/BerriAI/litellm/pull/48412) and
  [#48462](https://github.com/BerriAI/litellm/pull/48462)) are open to fix it.
- **Is the trigger supported usage?** Yes. Standard chat completions streaming on supported OpenRouter models.
- **Is a real boundary crossed?** Yes. Wire corruption: an essential protocol chunk delivered by upstream
  is discarded before reaching the client, producing invalid conversation history.
- **What fix would a maintainer ship?** Inspect `reasoning_details` in `is_chunk_non_empty` and ensure
  the proxy stream serializer retains `reasoning_details` on forwarded chunks.

Record label: `bug`.

## Wire evidence

### Direct upstream stream (`upstream.http`)

The provider emits the signature-only delta chunk in the SSE stream:

```http
data: {"id":"gen-test-117","object":"chat.completion.chunk","created":1730000000,"model":"anthropic/claude-sonnet-4.5","choices":[{"index":0,"delta":{"role":"assistant","reasoning":"Let me solve this step by step.","reasoning_details":[{"type":"reasoning.text","text":"Let me solve this step by step."}]}}]}

data: {"id":"gen-test-117","object":"chat.completion.chunk","created":1730000000,"model":"anthropic/claude-sonnet-4.5","choices":[{"index":0,"delta":{"reasoning_details":[{"type":"reasoning.text","signature":"SIG-claude-thought-signature-12345"}]}}]}

data: {"id":"gen-test-117","object":"chat.completion.chunk","created":1730000000,"model":"anthropic/claude-sonnet-4.5","choices":[{"index":0,"delta":{"content":"The answer is 42."},"finish_reason":"stop"}]}

data: [DONE]
```

### Observed from LiteLLM proxy (`observed.http`)

LiteLLM proxy silently drops the second chunk; the client receives only text and content:

```http
data: {"id":"gen-test-117","created":1793517300,"model":"claude-sonnet-4.5","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"reasoning_content":"Let me solve this step by step.","reasoning":"Let me solve this step by step.","reasoning_details":[{"type":"reasoning.text","text":"Let me solve this step by step."}],"role":"assistant"}}]}

data: {"id":"gen-test-117","object":"chat.completion.chunk","created":1793517300,"model":"claude-sonnet-4.5","choices":[{"index":0,"delta":{"content":"The answer is 42."}}]}

data: {"id":"gen-test-117","object":"chat.completion.chunk","created":1793517300,"model":"claude-sonnet-4.5","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

### Expected from LiteLLM proxy (`expected.http`)

LiteLLM proxy must forward the signature chunk intact so clients can reassemble valid thinking blocks:

```http
data: {"id":"gen-test-117","created":1793517300,"model":"claude-sonnet-4.5","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"reasoning_details":[{"type":"reasoning.text","signature":"SIG-claude-thought-signature-12345"}]}}]}
```

## Differential controls

Across 5 of 5 runs (see `transcripts/117/wire/results.json`):

1. **Direct upstream stream**: 5 of 5 runs preserve the signature delta chunk.
2. **LiteLLM proxy stream**: 5 of 5 runs silently drop the signature delta chunk (the defect).
3. **Next-turn follow-up with signature**: 5 of 5 runs succeed with HTTP 200 OK.
4. **Next-turn follow-up missing signature**: 5 of 5 runs rejected with HTTP 400 Bad Request.

## Root cause

1. **In `litellm/litellm_core_utils/streaming_handler.py:971` (`is_chunk_non_empty`)**:
   `is_chunk_non_empty` checks `content`, `tool_calls`, `function_call`, `reasoning_content`,
   `provider_specific_fields`, `annotations`, `role`, and `reasoning_items`. It never inspects
   `reasoning_details`. For chunks containing only `delta.reasoning_details` (such as signature pieces
   or encrypted reasoning tokens), all conditions evaluate to False, and the chunk is discarded.
2. **In `litellm/proxy/proxy_server.py:10086-10107` (`_fast_serialize_simple_model_response_stream`)**:
   `unsupported_delta_fields` fails to list `reasoning_details`. Consequently, the fast serializer does not
   fall back to Pydantic serialization and instead executes the minimal serializer which only outputs
   `role` and `content`, dropping `reasoning_details`.

## Invariant and test

Invariant: In an OpenRouter Chat Completions SSE stream containing reasoning details (such as
signatures or encrypted reasoning tokens), chunks carrying `reasoning_details` must not be dropped
and must reach the client.

Harness checker: `openrouter_stream_preserves_reasoning_details` in
`crates/harness/src/checks.rs`.
Conformance tests: `litellm_openrouter_streaming_signature_dropped_is_violation`,
`litellm_openrouter_direct_upstream_preserves_signature_is_conformant`, and
`litellm_openrouter_results_matrix_5_runs_reproduce_consistently` in `crates/harness/tests/conformance.rs`.
