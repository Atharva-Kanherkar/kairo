# 064-litellm-drops-tool-strict - Test Contract

## Functional Behavior

- An Anthropic `/v1/messages` request with `tools[0].strict: true` must forward an equivalent `strict: true` field on the OpenAI Responses function tool.
- The recorded LiteLLM 1.96.2 capture must be reported as a violation because all five forwarded Responses tools omit `strict` while the client receives HTTP 200.
- A direct Responses tool and LiteLLM's OpenAI Chat ingress must preserve `strict: true`; these controls show the target spelling and same proxy can carry the field.
- The issue artifact must not include real credentials or duplicate an existing Kairo issue.

## Unit Tests

- `tool_strict_forwarded` reports `Violation` when a captured Responses function tool omits `strict`.
- `tool_strict_forwarded` reports `Conformant` for Responses and OpenAI Chat control tool shapes that contain `strict: true`.

## Integration Tests

- `litellm_drops_anthropic_tool_strictness` checks the five-run LiteLLM capture with the generic checker.
- Control tests validate the direct Responses and same-proxy OpenAI Chat captures.
- `cargo test` passes for the complete Kairo harness.

## Smoke Tests

- `cargo test --test conformance litellm_tool_strict` passes.
- `python3 tools/update-readme-counts.py` leaves README status counters current.

## E2E Tests

N/A. The issue is proven using a keyless local capture rig and recorded wire bytes.

## Manual Tests

- Start LiteLLM 1.96.2 against a local OpenAI-compatible capture server that returns a valid Responses payload.
- Send the Anthropic `tools[0].strict: true` request five times and verify each forwarded `/v1/responses` tool lacks `strict`, while every client response is HTTP 200.
- Send an OpenAI Chat request with `tools[0].function.strict: true` and verify the same proxy forwards `strict: true`.
