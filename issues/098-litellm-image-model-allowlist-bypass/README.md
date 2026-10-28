# 098, A virtual key scoped to one model is served any model named in the image route URL, because auth and the handler parse the request differently

- **Upstream**: [BerriAI/litellm#41810](https://github.com/BerriAI/litellm/issues/41810) (open) reports the same class on `/v1/chat/completions?model=`; [BerriAI/litellm#41852](https://github.com/BerriAI/litellm/pull/41852) (open, unmerged) fixes chat/completions, completions and embeddings only. No upstream issue or pull request covers the image routes. State at 2026-10-27, details in Gate 3.
- **Tool under test**: `BerriAI/litellm` commit [`cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a`](https://github.com/BerriAI/litellm/commit/cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a), `pyproject.toml` version 1.108.0 (2026-10-26). This commit was `main` HEAD by `git ls-remote` on 2026-10-27. On 2026-10-28 `main` had advanced to `1419343`, then `0657ed46` by the reviewer's final check; the auth, parsing, image handler, and common request processing files cited here are unchanged across those commits.
- **Reproduced**: 2026-10-27T17:16Z, macOS arm64, real LiteLLM proxy with a PostgreSQL-backed virtual key, one uvicorn worker, two deterministic loopback OpenAI-compatible upstreams, default settings plus `drop_params: true`. No provider credentials are used.

## What breaks

A virtual key's `models` list is the operator's per-key model scope. LiteLLM
enforces it on the body model: a request whose body names `denied-model` gets
`403 key_model_access_denied` and never reaches an upstream.

The image handlers also bind `model` from the request target, `?model=` on
`/v1/images/generations` and `/v1/images/edits`, or the Azure-compatible
`/openai/deployments/{model:path}/images/...` segment, and
`resolve_inference_model` gives that value precedence over the body model. The
access check and the handler therefore disagree about which model the request
targets. Send a body model the key is allowed to use and a different model in
the URL: auth approves the body model, the handler routes on the URL model, and
the key is served a model outside its scope. The response is produced by the
denied deployment and the proxy advertises that deployment in its own response
headers.

The affected user is the proxy operator who hands out scoped keys, for example
a key limited to a cheap image model for one tenant or application. The key
holder can reach every image model configured on the proxy by naming it in the
URL, so the allowlist stops bounding cost and model access. This is a policy
bypass, not a cross-tenant data disclosure: the caller must already hold a
valid key, and the upstream is one the same proxy is configured to serve.

## Wire evidence

The full exchange for every case and run is under
[`transcripts/098/`](../../transcripts/098/). Quoted from `runs/run-01`.

The bypass, generation via the query string:

```http
POST /v1/images/generations?model=denied-model HTTP/1.1
Authorization: Bearer <VIRTUAL_KEY>
Content-Type: application/json

{"model":"allowed-model","prompt":"a red cube"}
```

```http
HTTP/1.1 200 OK
x-litellm-model-api-base: http://127.0.0.1:58169/v1
...
{"data":[{"revised_prompt":"SERVED_BY_DENIED_IMAGE","url":"https://example.invalid/DENIED.png"}]}
```

The per-case denied ledger for that run recorded exactly one call, and its
forwarded body decodes to `{"prompt":"a red cube","model":"fake-model"}`. The
allowed ledger slice is empty. The `x-litellm-model-api-base` port is the
`denied-model` deployment's `api_base` from that run's `config.yaml`, not the
allowed deployment's.

The body-only control, same key and same proxy:

```http
POST /v1/images/generations HTTP/1.1

{"model":"denied-model","prompt":"a red cube"}
```

```http
HTTP/1.1 403 Forbidden

{"error":{"message":"The requested model 'denied-model' is not available for this API key, or the model name is invalid. ...","type":"key_model_access_denied","param":"model","code":"403"}}
```

No upstream call is recorded for that control.

The edit route behaves the same way. `POST /v1/images/edits?model=denied-model`
with multipart field `model=allowed-model` returns 200 with
`x-litellm-model-group: denied-model` and
`x-litellm-model-api-base: http://127.0.0.1:58169/v1`, and the denied upstream
received a multipart body whose `model` field is `fake-model`.
`POST /openai/deployments/denied-model/images/edits` with the same multipart
body returns the same result.

## Root cause

Two parsers read the same request.

1. Auth reads `request_data["model"]`. `_read_request_body_deferring_parse_failure`
   (`litellm/proxy/auth/user_api_key_auth.py:1996`) calls
   `populate_request_with_path_params`
   (`litellm/proxy/common_utils/http_parsing_utils.py:750-790`), which copies
   query parameters into the parsed body with `setdefault`, so a body model
   suppresses a same-named query model. `get_model_from_request`
   (`litellm/proxy/auth/auth_utils.py:2270`) then reads only
   `request_data.get("model")` (`auth_utils.py:2192-2193`) for these routes.
2. The handler resolves the model again and prefers the URL.
   `image_generation` declares `model: str | None = None`
   (`litellm/proxy/image_endpoints/endpoints.py:88-92`) and calls
   `resolve_inference_model(..., kind="image_generation")` at
   `endpoints.py:125`. `image_edit_api` declares the same parameter
   (`endpoints.py:245-253`) and `ProxyBaseLLMRequestProcessing` resolves it
   with `kind="image_edit"` at
   `litellm/proxy/common_request_processing.py:2344-2350`. For generation the
   precedence is CLI model, then the URL value, then the configured image
   model, then the body; for edits a configured completion model comes first,
   then the same chain (`http_parsing_utils.py:48-57`). The URL model wins
   whenever it is present and no higher source is configured.
3. Both URL forms have a partial safety net that a body model suppresses. For
   the query form, `populate_request_with_path_params` copies the query value
   into the body with `setdefault`, so auth sees it only when the body is
   silent. For the deployment path, `get_model_from_request` parses
   `/openai/deployments/{model}` from the route only when no model candidate
   exists (`auth_utils.py:2306-2311`), which is why a path request with no body
   model is rejected 403. A body model makes both fallbacks skip, and the
   handler still routes on the URL value.

The open fix #41852 adds URL-bound model candidates, but its route tables
enumerate `/v1/chat/completions`, `/chat/completions`,
`/queue/chat/completions`, `/v1/completions`, `/completions`,
`/v1/embeddings`, `/embeddings` and their `{model:path}` deployment forms. The
image templates are absent from both sets, so merging that PR would leave this
bypass in place.

## Gate 1: correctness

### Exact reproduction

```text
cd /path/to/litellm-main    # cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a
PYTHONPATH=/path/to/litellm-main \
DATABASE_URL=postgresql://user@127.0.0.1:5432/litellm_kairo \
PRISMA_QUERY_ENGINE_BINARY=/path/to/query-engine-darwin-arm64 \
/path/to/venv/bin/python transcripts/098/reproduce.py \
  --litellm-source /path/to/litellm-main
```

The script starts two loopback upstreams, writes `transcripts/098/config.yaml`,
starts the real proxy with the pinned checkout, generates a Postgres-backed
virtual key with `models: ["allowed-model"]`, sends every case five times, and
writes the raw client and upstream bytes under `runs/run-01..05/`. The database
must already have the LiteLLM schema. Only environment variable names are
listed here.

- Expected behavior: a request whose effective model is outside the key's
  `models` list returns `403 key_model_access_denied` before any upstream call,
  whichever request field names the model.
- Observed behavior: a body model inside the list plus a URL model outside it
  returns 200, and the denied deployment serves the request.
- Reproduction rate: 55/55 requests across 11 cases and 5 runs. Each bypass
  case is 5/5, each control is 5/5, and the two body-silent cases are 5/5.
- Smallest isolated trigger: keep the request legal and remove one field at a
  time. Removing the URL model makes the request allowed; removing the body
  model makes auth reject the same URL model with 403. The bypass needs both: a
  body model auth accepts and a URL model the handler prefers.

### Control ladder

All rows are the same proxy, the same key and the same upstream pair. Only the
model field under test changes.

| Rung | Result | Evidence |
|---|---|---|
| Body model denied, no URL model | 403, no upstream, 5/5 | `gen_body_denied` |
| Body model allowed, no URL model | 200, allowed upstream, 5/5 | `gen_no_query` |
| Body model allowed, query model allowed | 200, allowed upstream, 5/5 | `gen_query_allowed` |
| Body model allowed, query model denied | 200, denied upstream, 5/5 | `gen_query_denied` |
| Body model allowed, deployment path denied | 200, denied upstream, 5/5 | `gen_deploy_denied` |
| Body model silent, query model denied | 403, no upstream, 5/5 | `gen_query_denied_nobody` |
| Body model silent, deployment path denied | 403, no upstream, 5/5 | `gen_deploy_denied_nobody` |
| Edit body model denied | 403, no upstream, 5/5 | `edit_body_denied` |
| Edit body model allowed, query model denied | 200, denied upstream, 5/5 | `edit_query_denied` |
| Edit body model allowed, deployment path denied | 200, denied upstream, 5/5 | `edit_deploy_denied` |
| Edit body model allowed, no URL model | 200, allowed upstream, 5/5 | `edit_no_query` |

The two body-silent rows are the sharpest controls. They show that auth does
see the URL model when the body is silent, so the defect is not "auth never
reads the URL". It is the precedence mismatch: a body model hides the URL model
from auth while the handler still routes on it.

### False-positive checks

- [x] Tested the exact claimed behavior. The served model is proven by the
      upstream ledger and by `x-litellm-model-api-base` /
      `x-litellm-model-group`, not by echoing the request body.
- [x] Pinned the target commit. `cfe0100e` was `main` HEAD by `git ls-remote`
      on 2026-10-27; `pyproject.toml` reports 1.108.0. The proxy's
      `x-litellm-version: 1.104.0` header is stale installed package metadata
      and is not the tested identity.
- [x] Ruled out bad configuration and malformed input. The routes, the JSON and
      multipart bodies, and the virtual key are valid; the same config serves
      every allowed control.
- [x] Ruled out model nondeterminism. The upstreams return fixed markers and
      never sample; the defect is the proxy's choice of deployment.
- [x] Ruled out a mock-only artifact. The proxy, auth, router and key store are
      the real pinned implementation. The loopback upstream only records which
      deployment was reached; it does not decide routing.
- [x] Sanitized the evidence. The virtual key is `<VIRTUAL_KEY>`; the upstream
      key is the literal `sk-dummy-upstream` from the generated config; no
      provider credential, private prompt, or real tenant data is present.

## Gate 2: usefulness

### Who gets bitten

- Affected user: a LiteLLM operator who issues per-key model allowlists on a
  proxy that serves more than one image model.
- Real workflow: the operator creates a key with
  `{"models": ["cheap-image-model"]}` for a tenant, an app, or a batch job, and
  relies on that list to bound which model the key can spend on. The caller
  uses the documented OpenAI-compatible image endpoints.
- Preconditions and likely frequency: auth enabled (the default for a virtual
  key), at least one other image model configured on the proxy (normal for a
  multi-model gateway), and a caller who knows or guesses its name. Every
  request that names the URL model is served it: 5/5 per bypass case in the
  rig, with no retries or timing dependence.

### Observable consequence

- User action: the key holder sends a valid image request with the allowed
  model in the body and a different configured model in `?model=` or the
  deployment path.
- Wire-level defect: auth approves the body model; the handler resolves the
  URL model and routes to that deployment.
- End-user or agent-level failure: the response is generated by a model the
  operator did not grant. The caller gets a working 200 and the denied model's
  output, so nothing fails closed or warns the operator. The operator's model
  scope and the cost boundary it represents are not enforced.
- Consumer-boundary demonstration: the response body carries the denied
  upstream's `SERVED_BY_DENIED_IMAGE` marker and the response headers name the
  denied deployment (`x-litellm-model-api-base`, and
  `x-litellm-model-group: denied-model` on the edit route).
- Measured impact: the denied upstream received the request and produced the
  response, 5/5 for generation via query and path, and 5/5 for edits via query
  and path.
- Inferred impact, clearly labeled: spend is attributed to the denied model
  group in the response headers, so the operator's per-model cost reporting is
  likely to charge the unallowed model. The spend ledger itself was not
  queried, and no price difference is claimed.

### Bug or not

- **Is the expected behavior really the spec?** Yes. LiteLLM documents virtual
  key `models` as the set of models a key may call, and the same proxy returns
  `403 key_model_access_denied` when the body names a denied model. The open
  fix PR #41852 states the consequence as "the key's `models` list does not
  bound which model the request is served by" and makes the URL-bound model
  part of the check. No example, test, or UI in the tree says image routes are
  exempt.
- **Have maintainers already ruled on it?** Yes. They shipped a security fix
  for the Azure deployment path in #8888 (merged 2025-02-28, closing #8780),
  which adds a URL-model check only when the body has no model. They have also
  ruled that URL-bound models are part of the allowlist contract for text
  routes (#41852). Nothing rules the image handlers exempt, and #8888's
  body-first order is the partial safety net this finding bypasses. The omission
  is the defect.
- **Is the trigger supported usage?** Yes. Documented image endpoints, the
  documented Azure-compatible deployment path, a normal virtual key, default
  auth, and valid request bodies.
- **Is a real boundary crossed?** Yes. The virtual key's model scope is a
  product-defined boundary with auth enabled. The caller is the key holder and
  the data is the operator's model access and spend policy, not another
  tenant's content.
- **What fix would a maintainer ship?** One sentence: add the image route
  templates (`/v1/images/generations`, `/images/generations`,
  `/v1/images/edits`, `/images/edits`, and
  `/openai/deployments/{model:path}/images/generations|edits`) to the
  URL-bound model candidates that `get_model_from_request` checks, the same way
  #41852 does for the text routes.
- **Label**: `bug`.

## Gate 3: upstream status

- Date checked: 2026-10-27 and 2026-10-28 (UTC).
- Upstream commit checked: `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a`, which
  `git ls-remote https://github.com/BerriAI/litellm.git refs/heads/main`
  returned on 2026-10-27. The tree at that commit contains every cited line.
  On 2026-10-28 `main` was `14193435b8672ed974ecc666038b336f03ab53a6`, one
  unrelated commit ahead. The GitHub compare API shows no change to
  `http_parsing_utils.py`, `auth_utils.py`, `user_api_key_auth.py`,
  `image_endpoints/endpoints.py`, or `common_request_processing.py` between the
  two commits.
- Search terms: `model allowlist bypass`, `images generations model allowlist`,
  `images edits query model`, `key_model_access_denied images`,
  `resolve_inference_model`, `?model= allowlist`,
  `openai/deployments images model`, `image route model scope`, `model in url`,
  plus issue and pull request search for each.
- Issues and pull requests searched: all open and closed results for the terms
  above, plus direct reads of #8780, #8888, #9913, #41810, #41852, #41295,
  #41467, #39998 and #40957.
- Releases, changelog, documentation and commits searched: the repository
  changelog, the image endpoint docs and examples, and the commits touching
  `image_endpoints/endpoints.py`, `http_parsing_utils.py` and `auth_utils.py`.

What exists upstream:

| Artifact | State | Relation to this claim |
|---|---|---|
| [litellm#8780](https://github.com/BerriAI/litellm/issues/8780) "Model Access Restrictions Not Enforced via URL Model Name" | closed 2025-02-28 by #8888 | earlier report of the same class on the Azure deployment path; establishes that URL models are in scope |
| [litellm#8888](https://github.com/BerriAI/litellm/pull/8888) "(security fix) - Enforce model access restrictions on Azure OpenAI route" | merged 2025-02-28 | adds `get_model_from_request`, which reads the URL model only when `request_data.get("model")` is `None`; this is the partial safety net a disagreeing body model bypasses |
| [litellm#9913](https://github.com/BerriAI/litellm/issues/9913) "model restriction doesn't work with model in url" | closed stale 2025-08-20 | earlier report of the same class; no fix merged |
| [litellm#41810](https://github.com/BerriAI/litellm/issues/41810) "Virtual-key model allowlist bypass on /v1/chat/completions via the `?model=` query string" | open since 2026-09-18 | same class, text route only; its repro and fix language match this defect |
| [litellm#41852](https://github.com/BerriAI/litellm/pull/41852) "fix(proxy): enforce key model allowlist on ?model= / {model} for chat, completions and embeddings" | open, unmerged, non-draft, last updated 2026-10-13, 2 files changed | adds URL-bound candidates for text routes only; its `_LLM_HANDLER_QUERY_MODEL_ROUTES` and `_LLM_HANDLER_PATH_MODEL_TEMPLATES` sets exclude every image route, so it does not fix this |
| [litellm#41295](https://github.com/BerriAI/litellm/issues/41295) "Virtual-key model allowlist bypass on the Azure pass-through route" | open | same class on a different route family; supports that URL-bound models are in scope, not an image fix |
| [litellm#41467](https://github.com/BerriAI/litellm/issues/41467) "Watsonx pass-through route bypasses virtual-key model allowlists" | open | same class on a pass-through route; adjacent |
| [litellm#39998](https://github.com/BerriAI/litellm/pull/39998) "re-check key/team model allowlists on server-side fallbacks" | open | allowlist enforcement on a different path; adjacent |
| [litellm#40957](https://github.com/BerriAI/litellm/pull/40957) "check the key allowlist on team alias targets" | open | alias resolution; adjacent |

Classification:

- [x] Novel for the exact claim
- [ ] Duplicate, open and still reproducible
- [ ] Fixed on current release
- [ ] Regression
- [ ] Documented behavior
- [ ] Discussed upstream without a dedicated ticket
- [ ] Incomplete, current upstream state could not be verified

The exact claim, image routes and image edit routes, is not filed. It is not a
duplicate of #41810, which is scoped to `/v1/chat/completions`, and it is not
covered by #41852's route tables. The adjacent reports are the reason this is
useful rather than a new class: the maintainers have already accepted the
invariant for text routes, and the same fix has to be extended to the image
handlers before it can be called complete.

## Frozen invariant

- The invariant, in words: a virtual key scoped to a set of models is never
  served by a model outside that set, whichever request field names the model.
- Checker added: `allowlist_bounds_served_model` in
  [`crates/harness/src/checks.rs`](../../crates/harness/src/checks.rs). It
  parses the request target (query model, or the deployment path segment, with
  the path winning where FastAPI binds it), falls back to the JSON or multipart
  body model, and requires `403` with no upstream call when the effective model
  is outside the key's set. It also requires a 2xx with an upstream call when
  the effective model is inside the set, so a gateway that rejects everything
  cannot pass.
- Conformance tests added: `litellm_098_*` in
  [`crates/harness/tests/conformance.rs`](../../crates/harness/tests/conformance.rs),
  replaying the recorded generation and edit requests. Two extra tests attach
  a real upstream ledger to a 403 control and an empty ledger to a 200 control,
  so the checker cannot pass by ignoring either half.
- Why the checker tests the invariant rather than one fixture: it computes the
  effective model from the request target and body, the two sources this finding
  exercises, and it checks both directions plus the upstream ledger. It does not
  model a configured `image_generation_model` or `completion_model`, which can
  outrank the URL for some kinds; the recorded config sets neither. The same
  checker would pass a fixed gateway and fail any gateway that lets the URL
  model escape the allowlist.

## Validation

| Check | Command | Result |
|---|---|---|
| Reproduction | `transcripts/098/reproduce.py` | PASS, 55/55 requests, 11 cases x 5 runs |
| Control | body-denied, allowed, and body-silent cases in the same script | PASS, 5/5 each |
| Harness | `cargo test --workspace` | PASS |
| Formatting | `cargo fmt --all -- --check` | PASS |
| Lint | `cargo clippy --workspace --all-targets -- -D warnings` | PASS |
| README counts | `python3 tools/update-readme-counts.py --check` | PASS |

## Security and scope

- [x] No API key, credential, private prompt, or unsanitized provider response
      is committed. The virtual key is redacted; the upstream key is a literal
      dummy in the generated config; every prompt is synthetic.
- [x] Only the local proxy and loopback upstreams were exercised. No hosted
      service, third-party tenant, rate limit, or real provider was touched.
- [x] The writeup states what was not verified: the spend ledger was not
      queried, and no price or billing difference is claimed.
- [x] The pull request contains one finding.
- [x] Unrelated working tree changes are preserved.

## Author verdict

- Correctness: PASS
- Usefulness: PASS
- Upstream status: PASS, classified `novel` with adjacent open reports linked
- Overall: ACCEPT

## Independent review

Verdict: **ACCEPT**, recorded 2026-10-28, using
`.github/agents/kairo-reproduction-reviewer.agent.md`.

- Correctness: PASS. The reviewer copied `transcripts/098` to a fresh temporary
  directory so the committed `runs/` could not be deleted, then reran the real
  pinned proxy on new free ports (proxy 59887, allowed 59888, denied 59889). The
  result was 55/55. The four bypass cases returned 200 with
  `x-litellm-model-api-base` pointing at the denied upstream; the 403 controls
  left the upstream ledgers empty. A structural comparison against the committed
  `runs/run-01` matched all 11 cases after normalizing ports, dates, UUIDs,
  durations, and multipart boundaries. The reviewer also mutated the harness
  fixtures in a temporary repo copy and confirmed the conformance tests fail for
  a bypass recorded as 403, a denied body recorded as 200, and an allowed case
  with an empty upstream ledger.
- Usefulness: PASS. The reviewer independently confirmed the 200 and the
  denied-upstream call per bypass case, and accepted the spend impact as
  measured for routing and inferred for billing.
- Upstream status: PASS, `novel`. The reviewer checked current `main`
  `14193435b8672ed974ecc666038b336f03ab53a6` and found the cited files
  unchanged, confirmed #41810 is open and text-only, and counted zero `images`
  occurrences in #41852's diff.
- Repository checks: PASS. `cargo fmt --all -- --check`,
  `cargo clippy --workspace --all-targets -- -D warnings`,
  `cargo test --workspace` (253 conformance, 81 unit after merging current
  `main`), and `python3 tools/update-readme-counts.py --check` all passed.
- Blocking findings: none.
- Post-merge re-verification, 2026-10-28: after `origin/main` advanced to
  `c1cad8f`, the reviewer confirmed the merge is an ancestor, the PR diff is
  limited to this issue, its transcripts, the 098 checker and tests, the
  SCOREBOARD row, and the README counts, and the 098 checker, helpers, and all
  nine tests are byte-identical to the reviewed revision. The focused tests pass
  9/9 and 2/2, the full suite passes 344/344 (253 conformance, 81 unit, 10
  adversarial), and formatting, lint, and the count check pass. The prior ACCEPT
  stands.

Non-blocking observations from the review, and how they are handled here: the
writeup now records the current-main advance and the earlier #8780/#8888/#9913
reports; the checker note above states that it does not model a configured image
or completion model, which the recorded config does not set. The reviewer also
noted that the edit route lacks body-silent controls symmetric with generation
and that the allowed case asserts an upstream call rather than the allowed
deployment identity. The generation body-silent controls and the source path
establish the mechanism, and the allowed upstream ledger is empty in every
bypass case, so neither gap changes the verdict.
