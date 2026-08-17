# Field preservation matrix

Run `20260817T112839Z`. 44 probes across 2 gateways, 88 cells.

This is the denominator. Every probe is a field or invariant that a cross-format gateway has to carry from the Anthropic `/v1/messages` ingress to an OpenAI-shaped backend. A cell that reads `OK` is a result, not an absence of one.


## Scope of this run, read before citing any number

This is a first validation run of the sweep rig, not a full census. Four
limits, each of which caps what the rates above can support:

1. **Two of five gateways.** Switchyard and GoModel were not installed on the
   machine, and AxonHub configures its channels in a database rather than a
   file. Those 132 cells are unmeasured, not clean.
2. **LiteLLM 1.83.9 is older than the 1.96.2 the LiteLLM issues were frozen
   against.** The host's Python is 3.9, which constrained the resolve. This is
   not a current-release column and must not be read as one.
3. **No live impact leg.** No provider keys were present, so every verdict here
   is about what reaches the backend, not what the loss costs a real run.
4. **Eight positive controls came back clean, and that is mostly a coverage
   artifact rather than a fix.** Issues 012, 009, and 004 were reproduced
   against *Gemini* backends; this rig points LiteLLM at an OpenAI-shaped mock,
   so those paths were never exercised. Probes are backend-format specific.

Point 4 generalises past this run. Both gateways translate the Anthropic
ingress onto the OpenAI **Responses** API, not Chat Completions: `messages`
arrives as `input`, `system` as `instructions` or a system-role entry inside
`input`, `max_tokens` as `max_output_tokens`, and a JSON schema as
`text.format`. A corpus written against Chat Completions spellings scores those
preserved fields as dropped. Twelve cells in an earlier run of this sweep were
false drops for exactly that reason.


## Preservation rate

| gateway | preserved | scored cells | rate | not run |
|---|---:|---:|---:|---:|
| bifrost | 34 | 44 | 77% | 0 |
| litellm | 32 | 44 | 73% | 0 |

`scored cells` excludes cells that were not run and cells whose probe errored, so the rate is over what was actually measured. The `not run` column is the honest remainder: a gateway that could not be started shows up here rather than disappearing from the table.


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

Four probes are inverted, and the matrix already accounts for the inversion: the three credential-leak headers and the invented-empty-text response check report `OK` when the gateway did **not** do the bad thing. A `DROP` on `header.client_authorization` means the client's credential was forwarded upstream.


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
| `tools[].strict` | OK | DROP 5/5 |
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

## Headers

| field | bifrost | litellm |
|---|---|---|
| `client Authorization must not leak` | OK | OK [020] |
| `client x-api-key must not leak` | OK | OK |
| `openai-organization must not leak` | OK | OK [026] |
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


## What this run did not cover

- Live impact leg not run: no provider keys in the environment.

