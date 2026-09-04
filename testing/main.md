# main - Test Contract

## Functional Behavior

- Pull requests require evidence for correctness, usefulness, and upstream status.
- Correctness requires a local reproduction against the named version, raw wire
  evidence, a successful control, deterministic results, and a narrowly isolated
  trigger.
- Usefulness requires a concrete affected user path and an observable consequence.
- Upstream status requires a current search for matching issues, pull requests,
  release notes, commits, and documentation, with links and checked dates.
- A repository reviewer agent independently repeats the reproduction, checks the
  evidence, looks for false attribution, and tries to break every claim.
- Root agent instructions make this review convention mandatory for future issue
  and pull request work.
- Secrets may be used from the local environment but must never be printed,
  recorded in transcripts, or committed.

## Unit Tests

- N/A. This change adds repository process files, not executable application code.

## Integration / Functional Tests

- Parse all added YAML frontmatter successfully.
- Confirm every required proof field appears in the pull request template.
- Confirm `AGENTS.md` and the reviewer profile agree on acceptance and rejection
  criteria.
- Confirm all repository-relative links in the new files resolve.

## Smoke Tests

- Confirm Git recognizes the pull request template at
  `.github/PULL_REQUEST_TEMPLATE.md`.
- Confirm GitHub Copilot recognizes the repository agent profile at
  `.github/agents/kairo-reproduction-reviewer.agent.md`.

## E2E Tests

- Manually fill the pull request template for a hypothetical bug and verify that a
  submitter cannot honestly claim readiness without addressing all three gates.
- Walk the reviewer instructions against the same hypothetical report and verify
  that the output produces separate correctness, usefulness, and upstream-status
  verdicts plus one overall decision.

## Manual / cURL Tests

- Run `git diff --check`.
- Run `cargo test --workspace` to verify the repository remains healthy.
- Run `python3 tools/update-readme-counts.py --check` if that option is supported;
  otherwise run the command used by CI for README count verification.
