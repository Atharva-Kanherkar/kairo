# 111, LiteLLM serves a virtual key a model outside its allowlist when the denied model is named in the `generateContent` or image-deployment URL path

- **Upstream**: [BerriAI/litellm](https://github.com/BerriAI/litellm). The same
  class on the plain chat routes is a known open bug:
  [#41810](https://github.com/BerriAI/litellm/issues/41810) ("Virtual-key model
  allowlist bypass on `/v1/chat/completions` via the `?model=` query string"),
  with open fix PR [#41852](https://github.com/BerriAI/litellm/pull/41852)
  ("enforce key model allowlist on `?model=` / `{model}` for chat, completions and
  embeddings"). Older, closed:
  [#8780](https://github.com/BerriAI/litellm/issues/8780). Neither #41810's report
  nor #41852's route lists cover the Google `:generateContent` /
  `:streamGenerateContent` routes or the `/openai/deployments/{model}/images/...`
  routes. With #41852 applied, those routes still bypass (fix-control below).
  No report found for the generateContent or image variant on 2026-10-27.
  Classification: `novel` for the generateContent and image-deployment variant;
  the chat variant is linked as `duplicate-open`.
- **Tool under test**: LiteLLM proxy `main` at
  [`cfe0100e`](https://github.com/BerriAI/litellm/commit/cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a)
  (2026-10-26, the tip the day of work), installed editable from that checkout.
  Latest release at the time is
  [`v1.106.4`](https://github.com/BerriAI/litellm/releases/tag/v1.106.4)
  (2026-10-25); the affected code is identical on it. Control: the same `main`
  with only [`rig/pr41852-auth_utils.diff`](../../transcripts/111/rig/pr41852-auth_utils.diff)
  (the open fix PR) applied.
- **Provider**: a deterministic local OpenAI-compatible upstream
  ([`rig/fake_upstream.py`](../../transcripts/111/rig/fake_upstream.py)). The claim
  is about which model the gateway dispatches under a key allowlist, so the
  evidence is the forwarded bytes and the spend log, not provider behavior. No
  provider credentials are used.
- **Reproduced**: 2026-10-27, macOS arm64, proxy with a master key, a Postgres
  spend database, and default settings. One virtual key scoped
  `models: ["cheap-model"]`. Two registered deployments, `cheap-model` and
  `premium-model`, both pointed at the same local upstream and distinguished only
  by their upstream model id and price, so the served model is visible in the
  bytes and in the spend log.
- **Label**: `bug`

## Claim

A virtual key scoped `models: ["cheap-model"]` is correctly denied `premium-model`
in the request body (`403 key_model_access_denied`). But on
`POST /v1beta/models/premium-model:generateContent` (and
`:streamGenerateContent`, and `POST /openai/deployments/premium-model/images/generations`)
with `premium-model` in the URL path and the allowed `cheap-model` in the body,
the proxy returns `200` and the request is served by, and billed as,
`premium-model`. The model dispatched is not bounded by the key's allowlist on
these routes.

Root cause: the access check and the router read the requested model from
different places.

- Auth's `get_model_from_request()` collects model candidates from the request
  body first (`litellm/proxy/auth/auth_utils.py:2192`, `body_model =
  request_data.get("model")`), and only falls back to a URL-path model when the
  body has none. With `cheap-model` in the body, the allowlist check sees the
  allowed model and passes.
- The `generateContent` handler then passes the URL path model into the request
  processor (`litellm/proxy/google_endpoints/endpoints.py:179`, `model=model_name`),
  and `resolve_inference_model(kind="completion")` gives that endpoint model
  precedence over the body model
  (`litellm/proxy/common_utils/http_parsing_utils.py:67`,
  `return settings.get("completion_model") or cli_model or endpoint_model or body_model`).
  So the request is routed to `premium-model`, the model the key was never
  allowed.

The image deployment handlers resolve the same way
(`litellm/proxy/image_endpoints/endpoints.py:125`,
`data["model"] = resolve_inference_model(...)` over the `{model:path}` route).

## What breaks

A proxy operator hands an external user a virtual key limited to one cheap or
local model. The user reaches an expensive model through the Gemini-compatible
`generateContent` route (or the image route) and the operator is billed, capped
only by the key's overall budget.

1. The operator creates a key with `POST /key/generate` and body
   `{"models": ["cheap-model"]}`.
2. The user sends `POST /v1beta/models/premium-model:generateContent` with
   `x-goog-api-key: <key>` and body `{"model": "cheap-model", "contents": [...]}`.
3. The proxy returns `200`, answered by `premium-model`.
4. The spend log records the call under `premium-model` against that key.

Measured here: with a `$1` budget on the key, five premium calls through the
bypass exhaust the budget (`budget_exceeded` on the sixth), because each premium
call bills `$0.2` against a key the operator scoped to a `$0.0002` model. The key
`models` list, the product's documented per-key model scope, does not bound the
model served.

## Wire evidence

All under [`transcripts/111`](../../transcripts/111), recorded by
[`rig/run.sh`](../../transcripts/111/rig/run.sh) +
[`rig/drive.py`](../../transcripts/111/rig/drive.py). Each run holds the client
request and response bytes and the exact forwarded upstream request and response,
with `Authorization` and `x-goog-api-key` replaced by `[REDACTED]` inline. `wire`
is stock `main`; `wire-pr41852` is `main` with the open fix PR applied.

Client request, `generateContent` bypass
([`wire/gc-path-denied-body-allowed/run-01/client-request.http`](../../transcripts/111/wire/gc-path-denied-body-allowed/run-01/client-request.http)):

```text
POST /v1beta/models/premium-model:generateContent HTTP/1.1
x-goog-api-key: [REDACTED]
content-type: application/json

{"model": "cheap-model", "contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
```

Forwarded upstream request for the same call
([`.../upstream/fwd-request.http`](../../transcripts/111/wire/gc-path-denied-body-allowed/run-01/upstream/fwd-request.http)):

```text
POST /v1/chat/completions HTTP/1.1
Authorization: [REDACTED]
...
{"model":"premium-upstream","messages":[{"role":"user","content":"hi"}]}
```

Client response: `200`, `served-by-upstream-model=premium-upstream`.

Body-model control, same key
([`wire/chat-body-denied/run-01/client-response.http`](../../transcripts/111/wire/chat-body-denied/run-01/client-response.http)):
`403 key_model_access_denied` for `{"model": "premium-model"}`.

### Results

Control ladder, N of N = 5, [`wire/results.json`](../../transcripts/111/wire/results.json)
and [`wire-pr41852/results.json`](../../transcripts/111/wire-pr41852/results.json).
`forwarded` is the model id in the forwarded upstream request (so the model
actually served); the key allowlist is `["cheap-model"]` throughout.

| Cell | route, path vs body model | `main` | `main` + PR #41852 |
|---|---|---|---|
| chat-body-denied | `/v1/chat/completions`, body=premium | 403, 5/5 | 403, 5/5 |
| chat-query | `/v1/chat/completions?model=premium`, body=cheap | 200 premium, 5/5 | **403, 5/5** |
| deployments-chat | `/openai/deployments/premium/chat/completions`, body=cheap | 200 premium, 5/5 | **403, 5/5** |
| **gc** | `/v1beta/models/premium:generateContent`, body=cheap | 200 premium, 5/5 | **200 premium, 5/5** |
| **gc-stream** | `:streamGenerateContent`, body=cheap | 200 premium, 5/5 | **200 premium, 5/5** |
| **images** | `/openai/deployments/premium/images/generations`, body=cheap | 200 premium, 5/5 | **200 premium, 5/5** |
| gc-trigger-removed | `/v1beta/models/cheap:generateContent`, body=cheap | 200 cheap, 5/5 | 200 cheap, 5/5 |
| messages-query | `/v1/messages?model=premium`, body=cheap | 200 cheap, 5/5 | 200 cheap, 5/5 |
| messages-body-denied | `/v1/messages`, body=premium | 403, 5/5 | 403, 5/5 |

The sibling chat routes (chat-query, deployments-chat) are closed by the open fix.
The `generateContent`, `streamGenerateContent` and image routes bypass on both
builds. The two negative controls behave correctly: removing the trigger serves
`cheap`, and `/v1/messages` ignores the query model and serves the body model
(as #41810 already notes for Anthropic and Responses).

Spend log for the `main` run,
[`wire/spend.json`](../../transcripts/111/wire/spend.json): all spend is on key
alias `cheap-only-111`; `premium-model` is billed across the bypass cells while
the key is scoped to `cheap-model`.

## Root cause

Two readers disagree about the requested model.

`litellm/proxy/auth/auth_utils.py` at `cfe0100e`, `get_model_from_request` ->
`_extract_model_candidates_from_request` (line 2192) reads the body `model` for
these routes and only derives a model from the URL path when the body has none
(the `/openai/deployments/{model}` regex and the Google path regex are gated on
`model is None`). The access check sees the allowed body model.

`litellm/proxy/google_endpoints/endpoints.py:179` and `:244` pass the URL path
model (`model=model_name`) into `base_process_llm_request`, and
`litellm/proxy/common_utils/http_parsing_utils.py:67` resolves
`endpoint_model or body_model`, so routing uses the path model. The image
handlers do the same at `litellm/proxy/image_endpoints/endpoints.py:125`. The
request is dispatched to a model the access check never authorized.

## Consumer boundary

Measured at the spend log and budget, the closest practical consumer of a key
model scope. The `cheap-only-111` key, scoped `models: ["cheap-model"]`, is billed
for `premium-model` on every `generateContent`, `streamGenerateContent` and image
bypass call (`wire/spend.json`), and a `$1` budget on such a key is drained by
five premium calls that the key should never have been able to make. Not measured:
a live Gemini or OpenAI provider (not needed; the defect is which model the
gateway dispatches under the allowlist, which the forwarded bytes show directly).
Inferred, not separately run: `/images/edits` and the unprefixed
`/models/{model}:generateContent` route share the same resolution path and are
expected to behave identically; `generations` and the `/v1beta/...` route were run.

## Bug or not

- **Spec**: a key's `models` allowlist is a documented per-key access control, and
  the body-model path enforces it (`403` above). The same class is treated as a
  bug by maintainers: issue #41810 is labeled a bug and PR #41852 is an open fix
  for it.
- **Maintainer ruling**: none exempts these routes. #41852 deliberately lists the
  chat, completions and embeddings route templates and leaves the Google and image
  routes out; no comment, test or denylist marks them as intended to ignore the
  allowlist. The fix-control run confirms they are still open after that PR.
- **Supported usage**: a default proxy with a master key, a documented
  Gemini-compatible route, and registered models. Not operator misuse.
- **Boundary crossed**: the key `models` allowlist scope. A key scoped to
  `cheap-model` is served, and billed for, `premium-model`.
- **Fix a maintainer would ship**: extend the URL-model allowlist check to the
  `:generateContent` / `:streamGenerateContent` and image deployment routes, the
  same way #41852 does for chat, completions and embeddings (add their route
  templates to the path-model candidate set so the dispatched model is checked).

## Upstream status

Checked 2026-10-27 against `main` `cfe0100e` and `v1.106.4`.

- BerriAI/litellm issue and PR search: "generateContent model allowlist bypass",
  "generateContent key models access", "images/generations model access", "URL
  model allowlist", "path model access check bypass", "key model access
  generateContent", and a listing of the model-access PRs. The only URL-model
  allowlist reports are #41810 and #41852, both scoped to chat / completions /
  embeddings; #8780 is the older closed chat variant; #34244, #41295, #41467 are
  separate passthrough-route variants; #39998 is fallback re-checking.
- Reading #41852's diff: its `_LLM_HANDLER_QUERY_MODEL_ROUTES` and
  `_LLM_HANDLER_PATH_MODEL_TEMPLATES` list only the chat / completions / embeddings
  templates. The Google and image routes are absent, which the fix-control run
  confirms.

Classification: `novel` (generateContent, streamGenerateContent, image
deployment routes), sibling `duplicate-open` (#41810, #41852).

## Test

[`served_model_within_key_allowlist`](../../crates/harness/src/checks.rs) and
`bug111_litellm_serves_a_model_outside_the_key_allowlist_via_the_url_path` in
[`crates/harness/tests/conformance.rs`](../../crates/harness/tests/conformance.rs).
Invariant: under a virtual key scoped to a model allowlist, a `200` response must
have been dispatched to a model group inside that allowlist. The conformance test
reads the committed forwarded bytes for every cell on both builds and asserts the
controls stay conformant, the sibling chat routes are fixed only on
`wire-pr41852`, and the `generateContent`, `streamGenerateContent` and image
routes violate the invariant on both builds.
