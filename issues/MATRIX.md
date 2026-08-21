# Field preservation matrix

Run `20260817T123216Z`. 48 probes across 2 gateways, 96 cells.

This is the denominator. Every probe is a field or invariant that a cross-format gateway has to carry from the Anthropic `/v1/messages` ingress to an OpenAI-shaped backend. A cell that reads `OK` is a result, not an absence of one.


## Scope of this run, read before citing any number

A validation run of the sweep rig, not a census. Two limits that the generated
sections below cannot derive on their own:

1. **Two of five gateways.** Switchyard and GoModel were not installed on the
   host, and AxonHub configures its channels in a database rather than a file.
   Those cells are unmeasured, not clean.
2. **LiteLLM 1.83.9 is older than the 1.96.2 its issues were frozen against.**
   The host Python is 3.9, which capped the resolve. This is not a
   current-release column and must not be read as one.

No live impact leg ran, so every verdict here is about what reaches the
backend, not what the loss costs a real run.

One structural finding worth carrying forward: both gateways translate the
Anthropic ingress onto the OpenAI **Responses** API, not Chat Completions.
`messages` arrives as `input`, `system` as `instructions` or a system-role
entry inside `input`, `max_tokens` as `max_output_tokens`, and a JSON schema
as `text.format`. A corpus written against Chat Completions spellings scores
those preserved fields as dropped. Twelve cells in an earlier run were false
drops for exactly that reason.


## Preservation rate

| gateway | preserved | measurable | rate | no equivalent (`na`) | not run |
|---|---:|---:|---:|---:|---:|
| bifrost | 33 | 43 | 77% | 5 | 0 |
| litellm | 28 | 42 | 67% | 6 | 0 |

`measurable` is the denominator: cells that were run, did not error, and had a target-format equivalent to survive into. `na` cells are counted separately because they are the format boundary rather than survival, and folding them into `preserved` would inflate the rate with fields that were never carryable. The `not run` column is the honest remainder: a gateway that could not be started shows up here rather than disappearing from the table.


## Legend

| token | meaning |
|---|---|
| `OK` | the field survived to the forwarded upstream request, or the response invariant held |
| `na` | no equivalent exists in the target format; recorded so the boundary is visible rather than assumed |
| `DROP` | the field was silently absent from what the gateway forwarded |
| `MANG` | the field survived but changed meaning (renamed, stringified, re-encoded, or invented) |
| `4xx` | the gateway rejected the request outright. Loud, not silent, and not the species this repo hunts |
| `err` | transport failure or a checker that raised. Not a finding |
| `--` | not run. Gateway unavailable, or the budget ran out before the cell |

Every probe in the **Credential handling** section is inverted, as is the invented-empty-text response check: `OK` means the gateway did **not** do the bad thing, and `DROP` means it did. A `DROP` on `cred.body.api_key` means the planted credential reached the upstream request.

Vector is part of the probe. 020 and 026 are JSON *body* bypasses, and both writeups state that the same-named HTTP headers are correctly dropped by default; a header probe there tests the control and can never reproduce the defect. 023 names `api-key` / `OpenAI-Organization` and 027 names `x-goog-api-key`, neither of which is the Anthropic `x-api-key`.


## Request parameters

| field | bifrost | litellm |
|---|---|---|
| `model` | OK | OK |
| `messages` | OK | OK |
| `max_tokens` | OK | OK |
| `system` | OK | OK |
| `stop_sequences` | DROP [032] 5/5 ctl | DROP [041] 5/5 ctl |
| `temperature` | OK | OK |
| `top_p` | OK | OK |
| `top_k` | na | na |
| `metadata.user_id` | OK | OK |
| `service_tier` | OK | OK |
| `stream` | 4xx 5/5 | OK |
| `output_config.format` | OK | OK |
| `output_format (deprecated spelling)` | OK | OK |
| `output_config.effort` | OK | DROP 5/5 |
| `thinking (adaptive)` | OK | DROP 5/5 |
| `thinking (disabled)` | OK | na |
| `thinking.budget_tokens (legacy)` | OK | OK |
| `tools[].input_schema` | OK | OK |
| `tools[].strict` | OK | DROP [064] 5/5 ctl |
| `tool_choice: auto` | OK | OK |
| `tool_choice: any -> required` | OK | OK |
| `tool_choice: tool(name)` | OK | OK |
| `tool_choice.disable_parallel_tool_use` | DROP [031] 5/5 ctl | DROP [017] 5/5 ctl |
| `mcp_servers` | na | na |
| `context_management` | na | na |
| `cache_control (top-level)` | na | na |

## Message content blocks

| field | bifrost | litellm |
|---|---|---|
| `image (base64)` | OK | OK [012] |
| `document (pdf base64)` | OK | DROP [018] 5/5 |
| `tool_result.is_error` | OK | OK [006] |
| `tool_result with image block` | OK | MANG [007] 5/5 |
| `thinking block in history` | DROP [033] 5/5 | OK [016] |
| `cache_control on a content block` | na | na |
| `tool_use.id round trip` | OK [037] | OK |

## Credential handling (inverted)

| field | bifrost | litellm |
|---|---|---|
| `body `api_key` must not become upstream auth` | OK | DROP [020] 5/5 |
| `body `extra_headers` must not reach the wire` | OK | DROP [026] 5/5 |
| `client `api-key` header must not be forwarded` | OK | OK |
| `client `x-goog-api-key` must not be forwarded` | OK | OK |
| `client `openai-organization` must not be forwarded` | OK | OK |
| `client `Authorization` must not be forwarded` | OK | OK |
| `client `x-api-key` must not be forwarded` | OK | OK |

## Headers

| field | bifrost | litellm |
|---|---|---|
| `anthropic-beta forwarded or mapped` | DROP 5/5 | DROP 5/5 |

## Response translation

| field | bifrost | litellm |
|---|---|---|
| `finish_reason stop -> end_turn` | OK | MANG [001/002] 5/5 |
| `finish_reason length -> max_tokens` | MANG [035] 5/5 | MANG 5/5 |
| `content_filter signal survives` | MANG [034] 5/5 | MANG 5/5 |
| `no invented empty text block` | OK | OK [009] |
| `upstream refusal content survives` | DROP [036] 5/5 | OK |
| `upstream tool_call id reaches the client` | MANG [037] 5/5 | OK [004] |
| `usage counts survive translation` | MANG 5/5 | MANG 5/5 |

A bracketed number is the kairo issue that documents this loss on **that** gateway. It is per gateway on purpose: 012 is a LiteLLM issue, so Bifrost passing the same probe is a result, not a regression.


`ctl` marks a cell where the same gateway's own OpenAI `/v1/chat/completions` ingress carried the field in the same process. That is the control leg: the mapping exists on this machine and the Anthropic path is not applying it.

Issue 064 is an independent LiteLLM 1.96.2 rerun. It does not change this
1.83.9 sweep's denominator or preservation-rate table.


## Positive controls

14 of 20 probes whose defect is documented for that specific gateway reproduced in this run. Those are the cells that say the rig is exercising the path it claims to.


The rest came back clean. **Do not read a clean control as a fix.** The likelier explanations, in order: the probe's backend format does not match the one the issue was frozen against (a defect reproduced against Gemini cannot be caught by an OpenAI-shaped mock), the probe uses a different vector than the writeup, or the version under test differs. Each one needs a human read against its issue folder before it means anything.


| gateway | issue | field |
|---|---|---|
| bifrost | 037 | `tool_use.id round trip` |
| litellm | 004 | `upstream tool_call id reaches the client` |
| litellm | 006 | `tool_result.is_error` |
| litellm | 009 | `no invented empty text block` |
| litellm | 012 | `image (base64)` |
| litellm | 016 | `thinking block in history` |


## What this run did not cover

- Live impact leg not run: no provider keys in the environment.
