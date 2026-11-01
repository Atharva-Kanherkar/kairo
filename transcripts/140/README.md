# Transcript 140: LiteLLM OpenRouter Stream Served Model Loss

## Overview

This transcript records wire evidence reproducing the defect where LiteLLM's `CustomStreamWrapper` overwrites the provider-reported served model with the requested router alias when streaming OpenRouter models (issue [BerriAI/litellm#46095](https://github.com/BerriAI/litellm/issues/46095), fixed in PR [BerriAI/litellm#49498](https://github.com/BerriAI/litellm/pull/49498)).

When a client queries OpenRouter using an auto-router model such as `openrouter/auto`:
1. Upstream OpenRouter streams SSE events where `model` identifies the actual model selected (for example `openai/gpt-4o-mini`).
2. In unpatched LiteLLM (`v1.109.0`), `CustomStreamWrapper` unconditionally overwrites `model_response.model = self.model`, wiping out the served model and replacing it with `"auto"` on every chunk.
3. `stream_chunk_builder()` constructs an assembled response inheriting `model = "auto"`, corrupting billing (`litellm.completion_cost()` crashes or reports 0) and observability logs.
4. Under PR #49498, `CustomStreamWrapper` preserves `self._served_openrouter_model()`, retaining `model = "openai/gpt-4o-mini"` across stream chunks and the assembled completion.

## Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a`)
- Target Upstream PR: [BerriAI/litellm#49498](https://github.com/BerriAI/litellm/pull/49498) (merged commit `36dbb228d94a3e180fee68862f1b20be7bc45ebd`)
- Upstream Issue: [BerriAI/litellm#46095](https://github.com/BerriAI/litellm/issues/46095)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-02

## Differential Control Ladder

1. **Failing Case (Unpatched Streaming Handler)**:
   - Input: Streaming completion request with `model="openrouter/auto"`.
   - Upstream: OpenRouter SSE stream returning `model: "openai/gpt-4o-mini"`.
   - Result: Emitted chunks and `stream_chunk_builder()` response have `model: "auto"`. Served model is lost.
2. **Control 1 (Patched Streaming Handler under PR #49498)**:
   - Input: Streaming completion request with `model="openrouter/auto"`.
   - Upstream: OpenRouter SSE stream returning `model: "openai/gpt-4o-mini"`.
   - Result: Emitted chunks and assembled response preserve `model: "openai/gpt-4o-mini"`.
3. **Control 2 (Direct Model Request)**:
   - Input: Streaming completion request with direct model `model="openrouter/openai/gpt-4o-mini"`.
   - Result: Requested and served models match; chunks and assembled response preserve `model: "openai/gpt-4o-mini"`.
4. **Control 3 (Non-Streaming Completion)**:
   - Input: Non-streaming completion request with `model="openrouter/auto"` (`stream=False`).
   - Result: LiteLLM returns `response.model == "openai/gpt-4o-mini"`, isolating the defect specifically to the streaming wrapper pipeline.

## Artifacts in `wire/`

- `bug-stream-served-model-lost.json`: Captured stream showing unpatched LiteLLM overwriting chunk and assembled response models with `"auto"`.
- `control-fixed-stream-served-model-preserved.json`: Captured stream under PR #49498 fix showing served model preserved as `"openai/gpt-4o-mini"`.
- `control-direct-stream-model.json`: Captured stream verifying direct model requests retain model identity.
- `control-nonstreaming-served-model-preserved.json`: Captured non-streaming response verifying that `stream=False` already returns `"openai/gpt-4o-mini"`.
- `results.json`: 5 of 5 runs producing identical results across all conditions.
