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

## Closed-source routers and hosted products

This section applies only when the target's supported behavior is available
through a vendor-hosted public API, but the implementation cannot be checked out
and no official self-hosted image or package is available. A difficult build or
an inconvenient setup does not make an open-source target closed source.

For an eligible target, this section replaces only the Correctness requirement
to run the target locally from a pinned checkout, package, or image. Every other
Correctness requirement and the complete Usefulness and Upstream status gates
still apply. Closed source is a different evidence topology, not a lower bar.

### Authorization and scope

- Test only through documented, supported entry points with an account and
  workspace the tester is authorized to use.
- Use dedicated test workloads, synthetic prompts, canary credentials, and
  accounts owned by or explicitly authorized for the test.
- Do not bypass access controls, probe another tenant, enumerate private data,
  evade rate limits, or exceed the vendor's published testing rules.
- Stop and use the vendor's security-reporting channel if a test could expose
  another customer's data or materially affect the service. Do not create
  cross-tenant impact merely to prove exploitability.

### Pin the observed deployment

A hosted service may change without a release. Record enough context to make the
claim honest and repeatable:

- Date and UTC time window, public base URL, route, request and response dialect,
  region if exposed, account or plan class, and every relevant feature flag.
- Requested model, observed serving model when exposed, routing policy and stage,
  fallback configuration, workload identifier, and route-decision headers.
- Raw HTTP or exact SDK version, provider configuration, and any build, release,
  request, trace, recipe, or policy identifier the service returns. Sanitize
  tenant-specific identifiers before committing them.
- The official documentation, API reference, examples, UI behavior, changelog,
  and status information used as the contract, with access dates.

If the service exposes no build or release identity, state that the finding is
against the deployment observed during the recorded time window. Do not imply it
applies to an unreproducible historical version or every region.

### Black-box correctness protocol

- Exercise the real public endpoint. A mock of the closed-source product can
  validate the harness, but it cannot reproduce a product defect.
- Save the exact sanitized client request and response bytes. When the product
  supports bring-your-own-key, a custom upstream, provider logs, or trace export,
  also save the forwarded request and upstream response. State plainly when the
  middle hop is not observable.
- Build a differential control ladder whenever the product permits it:
  1. Call the incumbent provider directly with the same meaningful input.
  2. Call the product's documented plain, bypass, or non-routing path.
  3. Call the suspected routing or transformation path with the workload stage
     and route decision recorded.
  4. Remove only the suspected trigger and repeat.
- Change one discriminating condition at a time. Keep the model, prompt, tools,
  streaming mode, account, region, and configuration fixed unless that condition
  is the variable under test.
- Run each case enough times to report N of N. For nondeterministic models, test
  protocol and structural invariants rather than natural-language equality.
- For learning or adaptive routers, do not mix Observe, Qualify, Shadow, and Route
  results. Pin or record the stage for every call and prove which recipe served it.
- For fallback and retry claims, identify every attempted provider call when logs
  or trace exports make that possible. Separate a correct fallback from duplicate
  execution, hidden retry, extra billing, and a second gateway's retry policy.
- Reproduce through a closest practical consumer such as an SDK or agent loop
  after the raw-wire comparison. SDK behavior is impact evidence, not a substitute
  for the bytes.

The target is isolated only when the controls rule out the provider, model,
client SDK, adjacent gateway, configuration, route stage, and transient outage as
plausible causes. If any remain viable, the Correctness gate is `NEEDS EVIDENCE`.

### Intentional transformations and bug classification

Some closed-source routers deliberately change models, prompts, tool definitions,
context, retries, or response selection. A byte difference is not itself a bug.

- Identify the product's documented invariant: for example schema validity,
  preserved tool-call protocol, a named fallback, a quality floor, or transparent
  pass-through on an unprefixed route.
- Check current documentation, examples, official clients, dashboard behavior,
  and maintainer statements before treating transformation as loss.
- Demonstrate a violation of that invariant and the consumer-visible consequence.
  A cheaper model producing different prose is expected behavior unless it breaks
  a promised constraint or measured quality gate.
- State internal mechanics only as inference. Without source, an official trace,
  or a maintainer confirmation, do not claim a private function, code path, or
  source-level root cause. Root cause may be unknown; exact layer attribution may
  not be.

The standard `bug`, `docs-defect`, `hardening`, `feature-request`, and
`operator-misuse` labels still apply. Only `bug` can pass Usefulness.

### Required closed-source evidence

In addition to the normal issue template, include:

1. A sanitized service fingerprint containing the observed deployment context.
2. Raw client-side request and response bytes for the failing case and controls.
3. Forwarded bytes or provider traces when available, otherwise an explicit
   statement that the middle hop is unobservable.
4. Route, recipe, policy, request, and trace metadata returned for each call,
   sanitized as necessary.
5. An N of N matrix covering direct, plain or bypass, routed, and trigger-removed
   cases that the product supports.
6. A consumer-boundary reproduction and a statement separating measured impact
   from inference.

Never publish private dashboard content, customer traffic, proprietary prompts,
credentials, or vendor correspondence without permission.

### Upstream status for a private product

- Search public documentation, changelogs, status history, release notes, support
  articles, public issue trackers, and reasonable exact-error synonyms on the day
  of the work.
- Report the finding through the vendor's appropriate support or security channel
  when public tracking is unavailable. Record the date and ticket identifier, but
  do not copy private correspondence into the repository without permission.
- Use the normal upstream classifications. If the vendor confirms a fix, rerun
  the public endpoint before calling it `fixed`.
- If neither public evidence nor an authorized vendor response can establish
  current status, the Upstream status gate remains incomplete.

### Closed-source decision rule

- `ACCEPT`: the public contract is current, the exact violation reproduces N of N,
  differential controls isolate the hosted product, a real consumer consequence
  is demonstrated, and upstream status is complete.
- `NEEDS EVIDENCE`: the behavior is real but deployment identity, routing stage,
  middle-hop attribution, controls, impact, or upstream status is incomplete.
- `REJECT`: the difference is documented transformation, provider or SDK behavior,
  unsupported usage, operator misuse, a transient service incident, or a defect
  no longer present on the current deployment.

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
