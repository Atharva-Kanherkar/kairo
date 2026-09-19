# docs/closed-source-target-policy - Test Contract

## Functional Behavior

- `AGENTS.md` contains a dedicated policy for hosted closed-source routers and
  products whose implementation cannot be checked out or run locally.
- The policy replaces the source identity, local execution, and version-drift
  procedures with hosted deployment fingerprinting for an eligible target; the
  correctness, usefulness, upstream-status, safety, artifact, and repository-check
  gates remain in force.
- The unconditional open-source bullets in `AGENTS.md` point to the closed-source
  exception instead of contradicting it.
- `.github/agents/kairo-reproduction-reviewer.agent.md` provides an independent
  black-box reproduction path for an eligible closed-source target.
- `.github/PULL_REQUEST_TEMPLATE.md` accepts either a pinned local artifact or a
  hosted service fingerprint and records the mandatory closed-source evidence.
- `CONTRIBUTING.md` routes eligible hosted targets to the closed-source capture
  method and no longer defines Correctness as local-only.
- `issues/TEMPLATE.md` accepts a hosted service fingerprint and time window when
  no source version or commit exists.
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
- Every unavailable control rung and unobservable hop must be named and justified;
  missing controls cannot silently satisfy attribution.
- Sanitization is limited, marked inline, and cannot conceal the disputed bytes.
- Existing open-source requirements and repository style remain unchanged.

## Unit Tests

N/A. This is repository policy documentation. Cross-file consistency is checked
manually in the integration review below rather than by runtime unit tests.

## Integration / Functional Tests

- Read the complete diff and verify that the new section does not weaken the
  existing three gates outside the narrowly defined closed-source exception.
- Search `AGENTS.md`, `CONTRIBUTING.md`, `issues/TEMPLATE.md`, `.github/agents/`,
  and `.github/PULL_REQUEST_TEMPLATE.md` for conflicting unconditional local-run,
  pinned-version, and version-drift language. Verify every occurrence either
  references or implements the closed-source exception.
- Verify every mandatory claim can be supported by artifacts Kairo can actually
  store under `transcripts/` and an issue writeup.
- Verify the closed-source decision language is a delta on the repository-wide
  decision rule, so repository checks remain mandatory.

## Smoke Tests

- `git diff --check` passes.
- `cargo test --workspace` passes.
- Formatting, lint, and README count checks used by the repository remain green.

## E2E Tests

N/A. No runtime or user-facing product path changes.

## Manual / cURL Tests

- Manually evaluate the policy against a representative private-beta hosted router:
  a supported API key and endpoint should make black-box verification possible
  without pretending its private implementation was inspected.
- Confirm the policy would keep a hosted-router finding at `NEEDS EVIDENCE` if the
  router cannot be separated from provider behavior, routing stage, or model
  randomness.
- No live service call is required for this documentation-only change.
