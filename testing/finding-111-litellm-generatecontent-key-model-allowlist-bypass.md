# finding/111-litellm-generatecontent-key-model-allowlist-bypass — Test Contract

Source of truth for what "done" means on finding 111. Locked before evidence
and writeup. Adapted to the kairo reproduction gates in `AGENTS.md`, not a
software feature.

## Claim under test

On LiteLLM proxy `main` (`cfe0100e`), a virtual key scoped `models:
["cheap-model"]` is denied `premium-model` in the request body (403
`key_model_access_denied`), but is served `premium-model` when `premium-model`
is the URL path model on the Google `:generateContent` / `:streamGenerateContent`
routes and on the `/openai/deployments/{model}/images/...` routes, as long as the
allowed model sits in the body so auth passes. The model actually dispatched is
not bounded by the key allowlist on these routes.

Root cause: auth's `get_model_from_request()` reads the body `model` for these
routes (allowed), while the handler passes the URL path model into
`resolve_inference_model(kind="completion")`, which gives the path/endpoint model
precedence over the body. The two readers disagree, so the access check and the
dispatch see different models.

## Functional behavior (gate 1: correctness)

Exact inputs -> expected outputs, proxy with a master key, default settings, key
scoped to `cheap-model` only, fake deterministic OpenAI upstream, Postgres spend
log.

- Body names the denied model, affected route: `403 key_model_access_denied`.
  (documented control path works)
- Body names the allowed model, URL path names the denied model, on
  `:generateContent`: `200`, upstream forwarded `premium-upstream`. VIOLATION.
- Same on `:streamGenerateContent`: `200`, forwarded `premium-upstream`. VIOLATION.
- Same on `/openai/deployments/premium-model/images/generations`: `200`,
  forwarded `premium-upstream`. VIOLATION.
- Trigger removed (body allowed, path allowed) on `:generateContent`: `200`,
  forwarded `cheap-upstream`. (the route itself is fine; only the mismatch leaks)
- Negative control, `/v1/messages?model=premium-model` body `cheap-model`:
  query model ignored, forwarded `cheap-upstream`. (route not affected, matches
  the behavior #41810 already documents for Anthropic/Responses)
- Negative control, `/v1/messages` body `premium-model`: `403`. (body check still
  fires)
- Spend log: the `generateContent` bypass call bills `premium-model` /
  `premium-upstream` against the `cheap-only` key.

Every reproduced cell must be N of N. Target N = 5.

## Control ladder

1. Direct-to-upstream is not meaningful for an access-control claim (the
   deployment serves whatever model it is pointed at); the discriminating control
   is the body-vs-path pairing, recorded below.
2. Documented plain path: body model = denied -> 403 (works).
3. Suspected path: body allowed + URL path denied -> 200 served denied (bug).
4. Trigger removed: body allowed + URL path allowed -> 200 served allowed.
5. Fix control: apply the open upstream fix PR #41852 (auth_utils.py only) and
   re-run. chat/`?model=`/engines/deployments -> 403; `:generateContent` and
   `/images/...` -> still 200. Proves the pending fix does not cover these routes.

## Usefulness (gate 2)

- Named consumer: a proxy operator hands an external user a key limited to one
  cheap or local model; the user reaches `premium-model` through the Gemini
  generateContent route and the operator is billed, capped only by the key's
  overall budget.
- Chain: user action (crafted URL) -> wire (auth checks body, dispatch uses path)
  -> observable failure (200 served by a model the key was never allowed, billed
  to the operator, recorded in the spend log under the denied model).
- Boundary crossed: the key `models` allowlist is the product's documented scope
  boundary; the key reaches a model outside it.
- Label must be `bug` (only `bug` passes usefulness).

## Bug or not (must all be yes)

- Spec: key `models` allowlist is a documented access control; the body-model
  path already enforces it (403). Confirmed by the 403 on body and by upstream
  issue #41810 + PR #41852 treating the same class as a bug.
- Maintainer ruling: maintainers accept the class as a bug (issue #41810
  confirmed, PR #41852 open). No comment, test, or denylist marks generateContent
  or image routes as exempt.
- Supported usage: default proxy, master key, a documented Gemini-compatible
  route, a registered model. Not operator misuse.
- Real boundary: key allowlist scope, crossed.
- Fix a maintainer would ship: extend the URL-model allowlist check to the
  `:generateContent` / `:streamGenerateContent` and image deployment routes, the
  same way #41852 does for chat/completions/embeddings. Code fix, not a doc edit.

## Upstream status (gate 3)

- Searched BerriAI/litellm issues and PRs on reproduction day for the generateContent
  and image-route variants.
- Sibling class is known and open: #41810 (bug), #41852 (open draft fix),
  #8780 (closed older URL variant), #41295 / #41467 (Azure / Watsonx passthrough).
- The `:generateContent` / `:streamGenerateContent` and `/images/...` deployment
  variants are not covered by #41810's report or #41852's route-template lists
  (verified by reading the diff and by the fix-control run, where they still 200).
- Classification: `novel` for the generateContent and image-route variant, with
  the sibling chat variant linked as `duplicate-open`.

## Unit / harness tests

- `crates/harness/src/checks.rs`: new invariant
  `served_model_within_key_allowlist(cell_json)` -> Verdict. Given a cell with the
  key allowlist, the client request, the HTTP status, and the upstream-forwarded
  model, FAIL iff a `200` was served a forwarded model outside the allowlist.
  States the invariant ("a 200 response is served only a model inside the calling
  key's allowlist"), not the bug.
- `crates/harness/tests/conformance.rs`: a test that feeds the committed bypass
  cell (fails: generateContent served premium outside allowlist) and the fixed /
  denied cells (pass). Asserts the invariant catches the real captured bytes.
- `cargo test --workspace`, fmt, clippy, README count checks green.

## Manual / cURL tests

Recorded in `transcripts/111/rig/` with runnable scripts and committed request /
response / forwarded bytes, credentials and key values redacted inline.

## Artifacts / repository checks

- `issues/111-.../README.md` per `issues/TEMPLATE.md`.
- `transcripts/111/` sanitized wire + control ladder + N-of-N matrix + spend-log
  evidence + fix-control run.
- PR via `.github/PULL_REQUEST_TEMPLATE.md`, SCOREBOARD row, README counts.
- Independent review with `.github/agents/kairo-reproduction-reviewer.agent.md`.

## Out of scope / not verified (state plainly)

- Live Gemini/Vertex provider: not needed; the claim is about which model the
  gateway dispatches under a key allowlist, provable with a local deterministic
  upstream. The forwarded bytes are the evidence, not provider behavior.
- Exact frequency of operators using per-model key scoping: not measured.
- Whether every image sub-route (`/images/edits`) and countTokens behaves
  identically: generations tested; edits/countTokens stated by code path, marked
  inferred where not run.
