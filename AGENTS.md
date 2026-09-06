# kairo Agent Rules

These rules apply to the entire repository. They are mandatory for every agent
that researches, adds, changes, or reviews a reproduced issue.

## The three required gates

Do not call an issue verified and do not recommend merging its pull request until
all three gates pass.

### 1. Correctness

Reproduce the exact claim, not a nearby symptom.

- Identify the cited upstream issue, exact target repository, release or commit,
  route, request dialect, response dialect, model, and relevant configuration.
- Run the target system locally from a pinned checkout, package version, or image.
  Install its real dependencies and exercise its real public entry point.
- Use provider credentials already supplied through the environment when the claim
  depends on live provider behavior. Use a deterministic local capture upstream
  when the claim concerns only what the gateway forwards.
- Never print, copy, persist, or commit a credential. Never enable shell tracing
  around secrets. Record environment variable names only. Sanitize all captures.
- Save raw request, response, and forwarded bytes under `transcripts/`. A summary,
  screenshot, or assertion is not wire evidence.
- Run a control with the same meaningful input. Depending on the claim, call the
  provider directly, use a known-good route, use a fixed version, or remove only
  the suspected trigger.
- Report N of N results and isolate the smallest trigger. A nondeterministic result
  is a lead, not a verified finding.
- Rule out version drift, configuration mistakes, model nondeterminism, mock-only
  behavior, and malformed input before attributing the defect to the gateway.

### 2. Usefulness

Prove that the defect matters outside the capture harness.

- Name the affected user or customer and their real workflow.
- Show the chain from user action to wire defect to observable failure.
- Demonstrate the consequence at the closest practical consumer boundary, such as
  an agent loop halting, a tool running twice, a safety constraint disappearing,
  private content becoming visible, or a model losing required context.
- State the conditions and likely frequency. Separate measured impact from
  inference.
- If no concrete user-visible consequence can be demonstrated or rigorously traced,
  the usefulness gate fails even when the bytes differ.

#### Bug or not

Before calling anything a bug, answer these in the writeup. Any `no` fails this
gate.

- Is the expected behavior really the spec? A docstring, comment, or doc line is
  not the spec when the same project's examples, tests, UI, or clients show the
  behavior is intended. Check all four before quoting a stale sentence.
- Have maintainers already ruled on it? If a recent commit, PR description, code
  comment, or denylist deliberately classifies the field or behavior the other way,
  the finding is a docs defect or feature request, not a bug.
- Is the trigger supported usage? The precondition must be a documented setup with
  default or recommended settings. A secret in a non-secret field, a disabled
  security control such as no master key, or an unsupported provider layout is
  operator misuse.
- Is a real boundary crossed? For any disclosure claim, name the role or key scope
  the product says must not see the data, then show that caller seeing it. With
  auth off, everything is visible by design and nothing is crossed.
- What fix would a maintainer ship? Write it in one sentence. If it is a docstring
  edit, a doc note, or "do not do that", it is not a bug. If the only real defect is
  an adjacent inconsistency, reframe the claim around that and re-run the gates.

Record exactly one label: `bug`, `docs-defect`, `hardening`, `feature-request`,
or `operator-misuse`. Only `bug` can pass usefulness. The other labels may still be
worth a note upstream, but not an issue in this repository.

### 3. Upstream status

Check the current upstream state on the day of the work.

- Search upstream issues and pull requests using the exact error, field names,
  endpoint, dialect pair, and reasonable synonyms.
- Search release notes, changelogs, documentation, and relevant commits.
- Record links, search terms, target version, and date checked.
- Classify the claim as `novel`, `duplicate-open`, `fixed`, `regression`,
  `documented-behavior`, or `discussed-no-ticket`.
- A current, independently reproduced duplicate can still be useful, but it must be
  labeled and linked. A bug fixed on the current release must not be presented as a
  current defect.
- If access to current upstream evidence is unavailable, report the gate as
  incomplete. Do not guess from cached knowledge.

## Required artifacts

Every issue pull request must:

1. Use `.github/PULL_REQUEST_TEMPLATE.md` without deleting required sections.
2. Follow `issues/TEMPLATE.md` for the issue writeup.
3. Include sanitized wire evidence and a working control.
4. Add or update invariant-level harness coverage when practical.
5. Keep `cargo test --workspace`, formatting, lint, and README count checks green.
6. Receive an independent review using
   `.github/agents/kairo-reproduction-reviewer.agent.md`.

The reviewer must rerun the critical path and try to falsify the claim. The
author's transcript and prose are evidence to inspect, not facts to repeat. Missing
evidence is not a pass. The reviewer is read-only and must not repair the pull
request while reviewing it.

## Decision rule

- `ACCEPT`: all three gates pass and repository checks pass.
- `NEEDS EVIDENCE`: the claim may be valid, but at least one gate lacks proof.
- `REJECT`: the exact claim does not reproduce, is attributed to the wrong layer,
  is already fixed on the tested current version, has no useful consequence, or
  carries a bug-or-not label other than `bug`.

Any single failed or incomplete gate blocks approval.

## Repository style

- Never commit secrets, private prompts, or unsanitized provider responses.
- Use plain, concise language and state what was not verified.
- Do not use em dashes in files, commits, or pull request text.
- Preserve unrelated working tree changes.
