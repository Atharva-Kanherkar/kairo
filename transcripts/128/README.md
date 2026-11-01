# Transcript 128: LiteLLM Provider Fake Streams Drop Requested Logprobs

## Overview

This transcript records wire evidence reproducing the defect where LiteLLM provider fake streams (such as structured output handling or providers without native streaming support) drop requested token probabilities (`logprobs`) from streaming response chunks (Upstream PR [BerriAI/litellm#49405](https://github.com/BerriAI/litellm/pull/49405)).

In `litellm/llms/base_llm/base_model_iterator.py`, `convert_model_response_to_streaming` transforms a non-streaming `ModelResponse` into a `ModelResponseStream` chunk:
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

`StreamingChoices` defines `logprobs: Optional[ChoiceLogprobs] = None`. Because `convert_model_response_to_streaming` never forwarded `choice.logprobs`, the replayed stream chunks carry `logprobs=None`.

When callers pass `stream=True, logprobs=True`:
- The upstream provider sends HTTP 200 containing choices with full token probability metadata (`ChoiceLogprobs` with `content`, `logprob`, `bytes`, and `top_logprobs`).
- Non-streaming calls (`stream=False`) correctly deliver the complete `logprobs` object.
- Streamed fake-stream calls (`stream=True`) deliver streaming chunks with `logprobs=None`, completely erasing token log probabilities.
- As a result, streaming and non-streaming calls with identical inputs disagree.

## Reproduction Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- Target Upstream PR: [BerriAI/litellm#49405](https://github.com/BerriAI/litellm/pull/49405)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-01

## Differential Control Ladder

1. **Failing Case (`stream=True, logprobs=True`)**:
   - Send chat completion with `response_format={"type": "json_object"}`, `logprobs=True`, `stream=True`.
   - Result: Upstream provider returns HTTP 200 with choice logprobs, but LiteLLM yields chunks with `logprobs=None`.
2. **Control 1 (Non-streaming call `stream=False`)**:
   - Send chat completion with identical parameters except `stream=False`.
   - Result: Response choice preserves complete `logprobs` object with score `-0.25`.
3. **Control 2 (Fixed PR #49405 stream conversion)**:
   - Convert provider response with `preserve_logprobs=True`.
   - Result: Streaming chunk choice preserves `logprobs` matching the non-streaming response.
4. **Control 3 (`stream=True, logprobs=False`)**:
   - Call with `logprobs=False`.
   - Result: Both streaming and non-streaming yield `None`.

## Artifacts in `wire/`

- `bug-streaming-logprobs-dropped.json`: Captured first chunk from streamed call showing `choices[0].logprobs = null`.
- `control-sync-logprobs-preserved.json`: Captured non-streaming response showing `choices[0].logprobs` populated.
- `control-fixed-fake-stream-logprobs-preserved.json`: Captured fixed stream chunk preserving `logprobs`.
- `control-streaming-logprobs-false-null.json`: Captured stream chunk when `logprobs=False`.
- `provider-upstream-response.json`: Captured mock provider HTTP 200 response fixture.
- `results.json`: 5 of 5 runs producing identical results across all 4 conditions.
