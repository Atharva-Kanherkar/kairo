# docs/closed-source-target-policy - Test Contract

## Functional Behavior

- `AGENTS.md` contains a dedicated policy for hosted closed-source routers and
  products whose implementation cannot be checked out or run locally.
- The policy explicitly replaces only the local-checkout requirement for an
  eligible closed-source target; the correctness, usefulness, upstream-status,
  safety, and artifact gates remain in force.
- The policy requires evidence that identifies the observed deployment, exercises
  the real supported public entry point, isolates the target with differential
  controls, and preserves raw sanitized wire bytes.
- The policy distinguishes externally observed behavior from inferred internal
  causes and prevents source-level root-cause claims without source evidence.
- The policy accounts for routing, fallback, retries, model nondeterminism,
  learning stages, and intentionally transformative products.
- The policy gives explicit `ACCEPT`, `NEEDS EVIDENCE`, and `REJECT` consequences
  for closed-source findings.
- The policy prohibits unauthorized testing, cross-tenant probing, secret
  disclosure, and violations of the target's published testing rules.
- Existing open-source requirements and repository style remain unchanged.

## Unit Tests

N/A. This is repository policy documentation, not executable behavior.

## Integration / Functional Tests

- Read the complete diff and verify that the new section does not weaken the
  existing three gates outside the narrowly defined closed-source exception.
- Search `AGENTS.md` for conflicting unconditional local-run language and verify
  the exception resolves it unambiguously.
- Verify every mandatory claim can be supported by artifacts Kairo can actually
  store under `transcripts/` and an issue writeup.

## Smoke Tests

- `git diff --check` passes.
- `cargo test --workspace` passes.
- Formatting, lint, and README count checks used by the repository remain green.

## E2E Tests

N/A. No runtime or user-facing product path changes.

## Manual / cURL Tests

- Manually evaluate the policy against EVO Router as a representative private-beta
  hosted router: a supported API key and endpoint should make black-box verification
  possible without pretending its private implementation was inspected.
- Confirm the policy would keep an EVO finding at `NEEDS EVIDENCE` if the router
  cannot be separated from provider behavior, routing stage, or model randomness.
- No live service call is required for this documentation-only change.
