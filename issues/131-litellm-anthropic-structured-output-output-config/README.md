# 131, LiteLLM Anthropic Provider Emits Structured Output as Top-Level output_format Instead of output_config.format

- **Upstream**: Open PR [BerriAI/litellm#49579](https://github.com/BerriAI/litellm/pull/49579), related issues #21635, #47563, checked 2026-11-01
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- **Reproduced**: 2026-11-01, macOS, Python 3.12, module `litellm.llms.anthropic.chat.transformation`

## Bug or not

- **Is the expected behavior really the spec?** Yes. Anthropic API specification and Anthropic-compatible backends (AWS Bedrock Claude, Google Cloud Vertex Claude, Anthropic-compatible gateway implementations) define structured outputs under `output_config.format`. Setting a top-level `output_format` is an unrecognized parameter that backends discard.
- **Have maintainers already ruled on it?** Yes. In PR #49579 (`fix(anthropic): send structured output as output_config.format`), maintainers and contributors recognized that emitting top-level `output_format` broke structured outputs across Anthropic routes, and corrected the transformation to nest under `output_config.format`.
- **Is the trigger supported usage?** Yes. Calling `litellm.completion(model="claude-3-5-haiku-20241022", response_format={"type": "json_schema", "json_schema": ...})` is the standard OpenAI-compatible way to request structured outputs through LiteLLM.
- **Is a real boundary crossed?** Yes. Wire protocol correctness and data integrity boundary. The gateway maps the schema to an invalid wire parameter location, causing the provider to ignore the schema constraint completely and emit unconstrained free-form text.
- **What fix would a maintainer ship?** Nest the structured output schema under `data["output_config"]["format"]` instead of assigning to `data["output_format"]` in `litellm/llms/anthropic/chat/transformation.py`.
- **Classification**: `bug`

## What breaks

When a developer or agent system invokes Claude models with structured outputs via LiteLLM:
1. The client requests a response adhering to a JSON schema using `response_format={"type": "json_schema", ...}`.
2. In `litellm/llms/anthropic/chat/transformation.py`, `AnthropicConfig.transform_request` maps the schema to top-level `output_format`.
3. The outbound HTTP request payload sent to the Anthropic API or Anthropic-compatible backends contains `"output_format": { ... }` instead of `"output_config": {"format": { ... }}`.
4. The backend ignores the unrecognized top-level `output_format` parameter without failing the request.
5. The model generates unstructured prose or markdown code blocks instead of strict JSON complying with the schema.
6. Downstream pipelines expecting valid structured JSON encounter parse failures or missing required fields.

## Wire evidence

- `bug-anthropic-top-level-output-format.json`: Captured outbound payload showing top-level `output_format` and missing `output_config`.
- `control-fixed-anthropic-output-config-format.json`: Captured outbound payload with PR #49579 fix showing `output_config.format`.
- `control-output-config-beside-effort.json`: Captured outbound payload demonstrating that `effort` and `format` coexist inside `output_config`.
- `control-no-structured-output.json`: Captured outbound payload for plain request without structured output.
- `results.json`: 5 of 5 runs reproducing 100% deterministically across all conditions.

## Root cause (if found)

In `litellm/llms/anthropic/chat/transformation.py`:
```python
if response_format is not None:
    output_format = self._transform_response_format(response_format)
    if output_format:
        data["output_format"] = output_format
```
The transformation assigns the transformed schema directly to top-level `data["output_format"]`. The fix migrates this to:
```python
if response_format is not None:
    output_format = self._transform_response_format(response_format)
    if output_format:
        if "output_config" not in data:
            data["output_config"] = {}
        data["output_config"]["format"] = output_format
```

## Test

- Invariant checker: `anthropic_structured_output_config_format` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_anthropic_structured_output_top_level_format_wire_reproduction`
  * `litellm_anthropic_fixed_output_config_format_preserved_control`
  * `litellm_anthropic_output_config_beside_effort_control`
  * `litellm_anthropic_no_structured_output_control`
  * `litellm_anthropic_structured_output_matrix_5_runs_reproduce_consistently`
