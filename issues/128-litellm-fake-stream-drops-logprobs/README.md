# 128, LiteLLM Provider Fake Streams Drop Requested Logprobs

- **Upstream**: [BerriAI/litellm#49405](https://github.com/BerriAI/litellm/pull/49405), open as of 2026-11-01
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- **Reproduced**: 2026-11-01, macOS, Python 3.12, module `litellm.llms.base_llm.base_model_iterator`

## Bug or not

- **Is the expected behavior really the spec?** Yes. OpenAI Chat Completions specification allows callers to request token probabilities via `logprobs=True` for both streaming and non-streaming completions. When an AI gateway replays a complete provider response as a stream (fake streaming for structured outputs or non-streaming providers), it must preserve the provider's token probability metadata on choice objects.
- **Have maintainers already ruled on it?** Yes. Acknowledged in open PR #49405 (`fix(streaming): preserve logprobs in provider fake streams`). Maintainers confirmed that provider fake streams incorrectly dropped token probabilities and that streaming and non-streaming responses must not disagree.
- **Is the trigger supported usage?** Yes. Calling `litellm.completion()` with `stream=True`, `logprobs=True`, and `response_format={"type": "json_object"}` (or any provider fake-stream route) is standard documented usage.
- **Is a real boundary crossed?** Yes. Contract and data fidelity boundary. The gateway drops requested token probability metadata from the streaming chunks, returning `null` logprobs to downstream consumers while non-streaming requests return the full metadata.
- **What fix would a maintainer ship?** Forward `choice.logprobs` when constructing `StreamingChoices` in `convert_model_response_to_streaming` for provider fake streams.
- **Classification**: `bug`

## What breaks

When an evaluation agent, guardrail pipeline, or uncertainty-filtering client requests token probabilities with streaming enabled (`stream=True, logprobs=True`) on routes that use provider fake streaming (such as structured outputs with `response_format` or providers simulated through `MockResponseIterator`):
1. The upstream provider returns a standard HTTP 200 JSON completion containing choices with complete `logprobs` metadata (`content`, `logprob`, `bytes`, and `top_logprobs`).
2. LiteLLM executes `convert_model_response_to_streaming(model_response)`.
3. In `base_model_iterator.py`, `convert_model_response_to_streaming` builds `StreamingChoices(index=choice.index, delta=..., finish_reason=choice.finish_reason)` without passing `logprobs`.
4. The client receives SSE streaming chunks where `choices[0].logprobs` is `null`.
5. Downstream uncertainty scorers, confidence gates, or hallucination detectors that inspect token probabilities crash with `TypeError`/`AttributeError` or receive empty probabilities, while identical non-streaming requests succeed.

## Wire evidence

- `bug-streaming-logprobs-dropped.json`: Captured first chunk from streamed call showing `choices[0].logprobs = null` despite upstream provider returning logprobs.
- `control-sync-logprobs-preserved.json`: Captured non-streaming response for identical request parameters showing `choices[0].logprobs` fully preserved with score `-0.25`.
- `control-fixed-fake-stream-logprobs-preserved.json`: Output of PR #49405 fix logic preserving choice logprobs on the streaming chunk.
- `control-streaming-logprobs-false-null.json`: Captured chunk when `logprobs=False`, confirming `null` is expected when not requested.
- `provider-upstream-response.json`: Captured mock provider HTTP 200 response fixture.
- `results.json`: 5 of 5 runs producing identical results across all 4 conditions.

## Root cause (if found)

In `litellm/llms/base_llm/base_model_iterator.py`, lines 60-68:
```python
streaming_choices: Final[list[StreamingChoices]] = []
for choice in model_response.choices:
    streaming_choices.append(
        StreamingChoices(
            index=choice.index,
            delta=_streaming_delta(cast(Choices, choice).message),
            finish_reason=choice.finish_reason,
        )
    )
```
`StreamingChoices` defines `logprobs: Optional[ChoiceLogprobs] = None`. Because `convert_model_response_to_streaming` did not forward `choice.logprobs`, `logprobs` was dropped from every choice during stream chunk construction.

## Test

- Invariant checker: `fake_stream_preserves_provider_logprobs` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_fake_stream_drops_logprobs_wire`
  * `litellm_fake_stream_sync_control_wire`
  * `litellm_fake_stream_fixed_control_wire`
  * `litellm_fake_stream_logprobs_false_control_wire`
  * `litellm_fake_stream_matrix_5_runs_reproduce_consistently`
