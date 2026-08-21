# 065-switchyard-responses-instruction-loss - Test Contract

## Functional Behavior

- Responses instruction arrays and inline `system` and `developer` messages must retain their precedence when forwarded to OpenAI Chat.
- The five-run current Switchyard capture must report a violation: the top-level instruction is absent and both inline instruction roles become `user`, despite completed client responses.
- A string `instructions` control must remain a preceding OpenAI Chat `system` message.

## Unit Tests

- A checker rejects missing or role-demoted instruction messages.
- The checker accepts the string-instruction control.

## Integration Tests

- A conformance test checks all five captures and their completed client results.
- `cargo test` passes for the full Kairo harness.

## Smoke Tests

- `cargo test --test conformance switchyard_responses_instruction` passes.
- `python3 tools/update-readme-counts.py --check` passes.

## E2E Tests

N/A. The keyless local capture rig exercises the full proxy translation path.

## Manual Tests

- Run the current Switchyard server with an OpenAI Chat capture backend.
- Send a Responses request containing array instructions plus inline `system` and `developer` messages five times.
- Confirm each forwarded Chat body contains only the three messages as `user`; compare with a string-instructions control.
