# 120, LiteLLM Anthropic streaming guardrails parse transport chunks as complete SSE frames, silently dropping text and bypassing content filters on fragmented chunks

- **Upstream**: [BerriAI/litellm](https://github.com/BerriAI/litellm). Reported in open issue
  [#49449](https://github.com/BerriAI/litellm/issues/49449) (filed 2026-10-31 / 2026-11-01).
  Classified `duplicate-open`.
- **Tool under test**: LiteLLM proxy `v1.106.6` / `1.108.0-dev` at commit
  `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` (2026-10-31), confirmed on main `3129e44848`.
- **Provider**: Deterministic local mock Anthropic server on `127.0.0.1:19998/v1/messages`
  implementing the Anthropic Messages streaming Server-Sent Events (SSE) protocol.
- **Clients**: Standard Anthropic Messages API streaming clients, `httpx` 0.28.1.
- **Reproduced**: 2026-11-01, macOS arm64, Python 3.12.13. 5 of 5 runs per case across
  2 differential conditions (10 requests total).
- **Label**: `bug`

## Claim

On streaming `POST /v1/messages` requests with a `post_call` guardrail attached (such as
`litellm_content_filter`, PII detection, or custom moderation rules), LiteLLM's Anthropic
guardrail translation layer assumes every raw transport chunk received from the upstream
HTTP connection contains one or more complete Server-Sent Events (SSE) frames. When a TCP,
TLS, or HTTP chunk boundary falls inside an SSE frame, `_extract_text_from_sse` splits the
frame across iterations, causing `json.loads` to fail with `JSONDecodeError`. LiteLLM logs
a warning and skips the event, silently dropping the text tokens from `text_so_far`. As a
result, the post-call guardrail inspects an incomplete text string, failing to detect
prohibited content or sensitive data and allowing blocked words or secrets to stream
directly to the end client.

## What breaks

Organizations deploy LiteLLM as an AI Gateway to enforce content moderation, data loss
prevention (DLP), and safety policies across LLM workloads:

1. An enterprise configures LiteLLM proxy with a post-call content filtering guardrail:
   `litellm_content_filter` with pattern rules configured to detect and block confidential
   tokens, API keys, or harmful content.
2. A client application (e.g. chat interface, internal agent, or customer-facing assistant)
   sends a streaming Anthropic request:
   `POST /v1/messages` with `stream: true`.
3. Upstream Anthropic emits Server-Sent Events (SSE) frames containing message deltas.
   In standard TCP/HTTP streaming, read buffers (e.g. 1 KB, 4 KB, 8 KB, 16 KB) or socket
   flushes fragment the byte stream arbitrarily. In particular, large frames such as
   `signature_delta` (5 to 12 KB base64 thinking signatures) or multi-token text blocks
   frequently span multiple transport chunks.
4. In `litellm/llms/anthropic/chat/guardrail_translation/handler.py`:
   `AnthropicMessagesHandler.get_streaming_string_so_far(responses_so_far)` iterates over
   `responses_so_far` and calls `_extract_text_from_sse(chunk)` on each raw `bytes` chunk
   independently:
   ```python
   text_so_far = ""
   for response in responses_so_far:
       if isinstance(response, bytes):
           text_so_far += self._extract_text_from_sse(response)
   ```
5. Inside `_extract_text_from_sse(sse_bytes)`:
   It naively splits `sse_bytes.decode("utf-8").split("\n\n")`.
   When a frame is split across chunks, Chunk 1 contains an incomplete `data: {"type": ...`
   JSON payload. `json.loads(data_line)` raises `json.JSONDecodeError`.
   LiteLLM logs:
   `WARNING: Failed to parse JSON from SSE data: {"type": "content_block_delta", ...`
   and skips the event (`text = ""`).
   Chunk 2 contains the trailing JSON fragment `..."}}\n\n`, which lacks `event:` or
   `data:` headers, so it is also ignored.
6. The entire text content of the fragmented frame is erased from `text_so_far`.
7. `process_output_streaming_response` passes `inputs={"texts": [string_so_far]}` to
   `guardrail_to_apply.apply_guardrail()`.
8. Because the blocked token or secret was in the fragmented frame, the guardrail never
   sees it. The guardrail check succeeds without raising a violation.
9. LiteLLM forwards the raw stream directly to the client, completely bypassing the
   safety filter.
10. In contrast, when the exact same stream is transmitted with frame-aligned chunks
    (control), the guardrail detects the blocked token and terminates the stream with
    HTTP 400 Bad Request.

## Bug or not

Before classifying this finding as a bug, the five usefulness questions from `AGENTS.md` are evaluated:

- **Is the expected behavior really the spec?** Yes. The W3C Server-Sent Events standard
  defines events as delimited by two consecutive line breaks (`\n\n`). Stream transport
  mechanisms (TCP, TLS, HTTP chunked transfer) do not align read buffers with application-layer
  message boundaries. A protocol parser consuming SSE over a byte stream must reassemble
  fragments before attempting JSON parsing. LiteLLM already provides `split_complete_sse_frames()`
  for this purpose, but only invoked it in the billing cost-injection path.
- **Have maintainers already ruled on it?** Open issue [#49449](https://github.com/BerriAI/litellm/issues/49449)
  was filed and confirmed by contributors, verifying fragmented text loss on main.
- **Is the trigger supported usage?** Yes. Streaming requests to Anthropic `/v1/messages`
  with standard `guardrails` configuration.
- **Is a real boundary crossed?** Yes. Security and content safety boundary. Prohibited
  content and confidential tokens that the gateway is configured to block are silently
  leaked to the client due to transport-level fragmentation.
- **What fix would a maintainer ship?** Reassemble raw streaming bytes into complete SSE
  frames before parsing in `_extract_text_from_sse` and `_check_streaming_has_ended`.

Record label: `bug`.

## Wire evidence

### Bug case: Proxy client response streaming prohibited token (`proxy-response-bug.txt`)

When upstream emits the SSE stream across fragmented transport reads, LiteLLM logs:
`WARNING: Failed to parse JSON from SSE data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Confidential payload: BLOCKE`

The guardrail fails to detect `BLOCKED_SECRET_TOKEN_49449`, and the proxy streams the
full prohibited payload to the client with HTTP 200:

```text
event: message_start
data: {"type": "message_start", "message": {"id": "msg_01", "type": "message", "role": "assistant", "model": "claude-3-5-sonnet", "content": []}}

event: content_block_start
data: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}

event: content_block_delta
data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Confidential payload: BLOCKED_SECRET_TOKEN_49449 inside stream."}}

event: content_block_stop
data: {"type": "content_block_stop", "index": 0}

event: message_delta
data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}

event: message_stop
data: {"type": "message_stop"}
```

### Control case: Proxy client response blocking prohibited token (`proxy-response-control.json`)

When the exact same SSE stream is delivered with intact frame boundaries, the guardrail
extracts the full text, detects `BLOCKED_SECRET_TOKEN_49449`, and terminates the stream
with HTTP 400 Bad Request:

```json
data: {"error": {"message": "Content blocked: blocked_secret pattern detected", "type": "invalid_request_error", "param": null, "code": "400", "provider_specific_fields": {"error": "Content blocked: blocked_secret pattern detected", "pattern": "blocked_secret", "guardrail_name": "test_content_filter", "guardrail_mode": "post_call"}}}

data: [DONE]
```

## Root cause

In `litellm/llms/anthropic/chat/guardrail_translation/handler.py`:

Lines 1757-1769:
```python
        text_so_far = ""
        for response in responses_so_far:
            # Handle raw bytes in SSE format
            if isinstance(response, bytes):
                text_so_far += self._extract_text_from_sse(response)
```

And lines 1783-1813 in `_extract_text_from_sse`:
```python
            sse_string: Final = sse_bytes.decode("utf-8")
            events: Final = sse_string.split("\n\n")

            for event in events:
                ...
                if event_type == "content_block_delta" and data_line:
                    try:
                        data: _AnthropicSSEEvent = json.loads(data_line)
                        delta: _AnthropicSSEDelta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text += delta.get("text", "")
                    except json.JSONDecodeError:
                        verbose_proxy_logger.warning("Failed to parse JSON from SSE data: %s", data_line)
```

Each chunk in `responses_so_far` is parsed independently without reassembling incomplete
events. Fragmented frames raise `json.JSONDecodeError`, dropping the text delta.
Similarly, `_check_streaming_has_ended` (lines 1842-1875) parses chunks independently,
failing to detect `message_delta` when `stop_reason` is split across chunks.

## Test

Harness invariant tests added to `crates/harness/src/checks.rs` and `crates/harness/tests/conformance.rs`:

- Invariant checker: `anthropic_stream_guardrail_evaluates_all_text` validates that
  SSE streams containing prohibited patterns are blocked regardless of transport chunking.
  When fragmented chunks cause an AI gateway to permit a stream carrying blocked patterns,
  the invariant raises a `Verdict::Violation`.
- Conformance tests verify the 5-of-5 reproduction results matrix in
  `transcripts/120/wire/results.json`.
