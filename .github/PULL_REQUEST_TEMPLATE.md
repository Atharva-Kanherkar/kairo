# Finding

## Claim

<!-- One exact sentence. Name the broken invariant and translation path. -->

- Upstream project:
- Cited upstream issue or report:
- Tested release or commit:
- Client dialect and endpoint:
- Backend dialect and provider or capture upstream:
- Model, if relevant:
- Relevant configuration:

## Gate 1: correctness

### Exact reproduction

<!--
Build and run the real target system locally. Give commands another reviewer can
run from a clean checkout. List secret environment variable names only, never their
values. Pin every external version.
-->

```text
# setup, build, start, and request commands
```

- Expected behavior:
- Observed behavior:
- Raw request evidence:
- Raw response evidence:
- Forwarded-request evidence, if applicable:
- Reproduction rate: <!-- N/N -->
- Smallest isolated trigger:

### Control

<!-- Use the same meaningful input. Change only the suspected layer or trigger. -->

```text
# direct-provider, known-good route, fixed-version, or trigger-removed control
```

- Control result:
- Why this attributes the failure to the claimed layer:

### False-positive checks

- [ ] Tested the exact cited behavior, not a similar symptom.
- [ ] Pinned and reported the target version or commit.
- [ ] Ruled out bad configuration and malformed input.
- [ ] Ruled out model nondeterminism or reported why it is irrelevant.
- [ ] Confirmed the failure is not created only by the mock or harness.
- [ ] Sanitized all recorded evidence.

## Gate 2: usefulness

### Who gets bitten

- Affected user or customer:
- Real workflow:
- Preconditions and likely frequency:

### Observable consequence

<!-- Show: user action -> wire defect -> consumer failure. -->

- User action:
- Wire-level defect:
- End-user or agent-level failure:
- Consumer-boundary demonstration or transcript:
- Measured impact:
- Inferred impact, clearly labeled:

<!-- If there is no concrete consequence, explain why this PR should remain a lead. -->

### Bug or not

<!-- See AGENTS.md, Gate 2, "Bug or not". Any "no" fails this gate. -->

- Expected behavior confirmed as intended spec, not a stale doc line (cite examples, tests, or UI):
- Maintainer ruling searched (commit, PR, comment, denylist) and result:
- Trigger is supported usage with default or recommended settings:
- Boundary crossed (role or key scope that must not see the data, with auth enabled):
- Fix a maintainer would ship, in one sentence:
- Label: <!-- bug / docs-defect / hardening / feature-request / operator-misuse -->

## Gate 3: upstream status

- Date checked:
- Upstream version checked:
- Search terms used:
- Issues searched:
- Pull requests searched:
- Releases, changelog, and documentation searched:
- Relevant commits searched:
- Matching links:

Classification:

- [ ] Novel
- [ ] Duplicate, open and still reproducible
- [ ] Fixed on current release
- [ ] Regression
- [ ] Documented behavior
- [ ] Discussed upstream without a dedicated ticket
- [ ] Incomplete, current upstream state could not be verified

Explain what is new or useful here if the report is already known upstream:

## Frozen invariant

- Issue writeup:
- Checker added or updated:
- Conformance test added or updated:
- Why the checker tests the invariant rather than one implementation detail:

## Validation

| Check | Command | Result |
|---|---|---|
| Reproduction | | |
| Control | | |
| Harness | `cargo test --workspace` | |
| Formatting | `cargo fmt --all -- --check` | |
| Lint | `cargo clippy --workspace --all-targets -- -D warnings` | |
| README counts | `python3 tools/update-readme-counts.py --check` | |

## Security and scope

- [ ] No API key, credential, private prompt, or unsanitized response is committed.
- [ ] Only environment variable names appear in commands and documentation.
- [ ] The pull request contains one finding.
- [ ] Unrelated generated files and local state are excluded.

## Author verdict

- Correctness: <!-- PASS / FAIL / INCOMPLETE -->
- Usefulness: <!-- PASS / FAIL / INCOMPLETE -->
- Upstream status: <!-- PASS / FAIL / INCOMPLETE -->
- Overall: <!-- ACCEPT / NEEDS EVIDENCE / REJECT -->

## Independent review

Run `.github/agents/kairo-reproduction-reviewer.agent.md` against this pull
request. Approval is blocked until the reviewer independently reruns the critical
path and all three gates pass.
