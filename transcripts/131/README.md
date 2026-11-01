# Transcript 131: LiteLLM Anthropic Provider Emits Structured Output as Top-Level output_format Instead of output_config.format

## Overview

This transcript records wire evidence reproducing the defect where LiteLLM's Anthropic adapter emits structured output schema definitions as a top-level `output_format` parameter in the outbound request body rather than nesting it under `output_config.format` (Upstream PR [BerriAI/litellm#49579](https://github.com/BerriAI/litellm/pull/49579), related to #21635 and #47563).

In `litellm/llms/anthropic/chat/transformation.py`, `AnthropicConfig.transform_request` processes `response_format`:
```python
if response_format is not None:
    output_format = self._transform_response_format(response_format)
    if output_format:
        data["output_format"] = output_format
```

The method sets `data["output_format"]` directly at the top level of the request payload. However, the Anthropic Messages API and Anthropic-compatible provider routes (such as AWS Bedrock Claude, Vertex AI Claude, and Anthropic-compatible gateways) expect structured output schemas inside `output_config: {"format": {"type": "json_schema", ...}}`.

When `output_format` is passed at the top level:
- Backends silently ignore unrecognized top-level parameters without returning an error.
- The model runs in free-form generation mode without schema enforcement.
- Instead of returning guaranteed JSON adhering to the specified schema, the model outputs free-form prose, explanations, or markdown code fences (````json ... ````).
- Downstream JSON parsers and structured output workflows fail with JSONDecodeError or schema validation errors.

## Reproduction Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- Target Upstream PR: [BerriAI/litellm#49579](https://github.com/BerriAI/litellm/pull/49579)
- Target Upstream Issues: #21635, #47563
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-01

## Differential Control Ladder

1. **Failing Case (`AnthropicConfig.transform_request` with structured `response_format`)**:
   - Transform a request with `response_format={"type": "json_schema", "json_schema": {"name": "city_schema", "schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"], "additionalProperties": False}}}`.
   - Result: Top-level `output_format` is set; `output_config` is absent.
2. **Control 1 (Fixed PR #49579 transformation)**:
   - Transform the same request using `FixedAnthropicConfig.transform_request`, which removes top-level `output_format` and sets `output_config["format"]`.
   - Result: `output_format` is not at top level; `output_config.format` contains the JSON schema definition.
3. **Control 2 (Output config beside effort)**:
   - When `output_config` already contains `effort: "low"` (from reasoning configuration), both `effort` and `format` coexist inside `output_config`.
   - Result: `output_config` retains both `effort` and `format`.
4. **Control 3 (Plain request without structured output)**:
   - Transform a request without `response_format`.
   - Result: Neither `output_format` nor `output_config.format` is present.

## Artifacts in `wire/`

- `bug-anthropic-top-level-output-format.json`: Captured outbound payload showing `output_format` at top level and missing `output_config`.
- `control-fixed-anthropic-output-config-format.json`: Captured outbound payload with PR #49579 fix showing `output_config.format`.
- `control-output-config-beside-effort.json`: Captured outbound payload showing `output_config` preserving both `effort` and `format`.
- `control-no-structured-output.json`: Captured outbound payload for plain request without structured output.
- `results.json`: 5 of 5 runs producing identical results across all 4 conditions.
