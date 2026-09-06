# 072, Bifrost forwards Anthropic forced-tool `any` to OpenAI instead of `required`

- **Upstream**: [maximhq/bifrost](https://github.com/maximhq/bifrost), no matching ticket found. Related [#4532](https://github.com/maximhq/bifrost/pull/4532) fixes OpenRouter server-tool stripping, not this enum translation.
- **Tool under test**: official [transports/v2.0.0](https://github.com/maximhq/bifrost/releases/tag/transports/v2.0.0), commit `e4a30d6041c0446603aea615bc5da340dac001b1`, core v1.8.3. The runner verifies embedded revision, clean build and core dependency before executing.
- **Reproduced**: 2026-09-06, macOS arm64, `gpt-4o`, Anthropic Python SDK 0.125.0. Both keyless capture and real OpenAI runs are retained. No claim is made about v1.6.11.
- **Review state**: repair self-reviewed and rerun successfully; separate independent review remains required before approval. Self-review is not independent approval.

## What breaks

An Anthropic SDK user routing to OpenAI through Bifrost can request at least one
tool call with `tool_choice: {"type": "any"}`. Bifrost serializes that as the
string `"any"` instead of OpenAI's `"required"`. The real OpenAI provider rejects
the request before generating a tool call.

The measured workflow is `reproduce.py`'s minimal SDK/tool-dispatch loop. Its
single tool is `get_weather`. With SDK retries disabled, each invalid request
raises `anthropic.BadRequestError` and dispatches zero tools. Changing only the
choice to `{"type":"tool","name":"get_weather"}` forces the same available tool,
returns HTTP 200 and dispatches it exactly once. The tool is a local deterministic
function, not a network action or model-generated executable code.

```text
Anthropic SDK -> real Bifrost -> recording relay -> real OpenAI
   type:any      string:any       unchanged body      HTTP 400
       <- BadRequestError, zero tool dispatches <-
```

The observed provider error on both routes is:

```json
{"error":{"message":"Invalid value: 'any'. Supported values are: 'none', 'auto', and 'required'.","type":"invalid_request_error","param":"tool_choice","code":"invalid_value"}}
```

This is not a claim that every agent crashes: a caller can catch the exception.
The measured consequence is that this tool-requesting step cannot execute its
tool. Production frequency and streaming behavior were not measured.

## Wire evidence

Each JSONL line is one complete correlated exchange. `client_request.body_raw`,
`forwarded_request.body_raw`, `upstream_response.body_raw` and
`client_response.body_raw` retain the actual UTF-8 bodies, including whitespace,
as JSON strings. Decoding those strings restores the bytes; they are not
reconstructed summaries. The parsed top-level `body` is only a checker convenience
and must equal the parsed forwarded raw body. Status, run number, route, selected
non-secret headers and SDK/tool-dispatch outcome are also retained.

All paths below are under `transcripts/072/`. Each file has five exchanges.

| Live evidence | Upstream | Provider/client status | SDK tool dispatch |
|---|---|---|---|
| `live/responses-any.jsonl` | Responses, `any` | 400/400, 5/5 | 0, 5/5 |
| `live/chat-any.jsonl` | Chat, `any` | 400/400, 5/5 | 0, 5/5 |
| `live/responses-named.jsonl` | Responses, named function | 200/200, 5/5 | 1, 5/5 |
| `live/chat-named.jsonl` | Chat, named function | 200/200, 5/5 | 1, 5/5 |
| `live/responses-required.jsonl` | Responses, `required` | 200/200, 5/5 | HTTP control, function call returned |
| `live/chat-required.jsonl` | Chat, `required` | 200/200, 5/5 | HTTP control, function call returned |
| `live/responses-direct-required.jsonl` | Direct OpenAI Responses | 200/200, 5/5 | Function call returned |
| `live/chat-direct-required.jsonl` | Direct OpenAI Chat | 200/200, 5/5 | Function call returned |

Direct controls replay the captured violating body with only `tool_choice`
changed to `required`, bypassing Bifrost. Named controls change only the client's
choice field, preserving the same model, prompt and sole available tool.

The matching `local/` captures isolate forwarding without a provider key; they
also include `responses-auto.jsonl` and `chat-auto.jsonl`, both preserved 5/5.
The local responder always returns a synthetic success, including for `any`.
Those HTTP 200s are explicitly not provider-rejection evidence. There are 50 local
and 40 live exchanges, not five summaries standing in for the full trial set.

`local/metadata.json` and `live/metadata.json` record the binary SHA-256, embedded
Go build/dependency metadata, SDK versions, date, model and completion flag.
`responses-config.json` uses the built-in OpenAI provider. `chat-config.json` uses
the documented custom OpenAI provider with Responses disabled and Chat enabled,
which activates Responses-to-Chat fallback. Both target a loopback relay.

### Credential handling

Only `OPENAI_API_KEY` is loaded, from the process environment or the project
`.env` using a dotenv parser without interpolation or shell evaluation. The real
key stays in relay memory. Bifrost and the SDK receive a non-secret placeholder.
Authenticated relay requests have a fixed `https://api.openai.com` destination,
preserve Bifrost's body bytes, and cannot follow redirects. Authorization,
account-identifying and cookie headers are excluded; bodies containing the key or
a likely key string are refused before persistence. Prompts contain only the
synthetic weather task. The `.env` file is neither edited nor copied.

## Root cause

`convertAnthropicToolChoiceToBifrost` maps Anthropic `any` to
`ResponsesToolChoiceTypeAny`, the string `"any"`. `ToOpenAIResponsesRequest`
copies `ResponsesParameters` without normalizing it. The Responses-to-Chat
conversion also copies the string, and OpenAI Chat serialization leaves it intact.
The shared schema marshalers emit the string as provided.

The reverse Anthropic conversion already handles both `Any` and `Required` as
Anthropic `type:any`. The shared schema intentionally includes other providers'
values; that does not make `any` valid on the OpenAI wire.

Source inspected at current `dev` commit `03ab391865710462302bbcf52dca2f32682b91b5`:

- [Anthropic ingress conversion](https://github.com/maximhq/bifrost/blob/03ab391865710462302bbcf52dca2f32682b91b5/core/providers/anthropic/responses.go#L8209-L8223).
- [OpenAI Responses parameter copy](https://github.com/maximhq/bifrost/blob/03ab391865710462302bbcf52dca2f32682b91b5/core/providers/openai/responses.go#L308).
- [Responses-to-Chat choice conversion](https://github.com/maximhq/bifrost/blob/03ab391865710462302bbcf52dca2f32682b91b5/core/schemas/mux.go#L320-L322).
- [OpenAI Chat parameter copy](https://github.com/maximhq/bifrost/blob/03ab391865710462302bbcf52dca2f32682b91b5/core/providers/openai/chat.go#L43-L45).

## Bug or not

- **Expected behavior is the spec:** [Anthropic's generated type](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/types/tool_choice_any_param.py) accepts `any`; [OpenAI's generated string choices](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/tool_choice_options.py) are `none`, `auto`, `required`. Live validation confirms the distinction.
- **Examples, tests and UI checked:** Bifrost's [SDK example](https://github.com/maximhq/bifrost/blob/03ab391865710462302bbcf52dca2f32682b91b5/docs/integrations/anthropic-sdk/overview.mdx#L74-L101) routes Anthropic clients to OpenAI; its [integration test](https://github.com/maximhq/bifrost/blob/03ab391865710462302bbcf52dca2f32682b91b5/tests/integrations/python/tests/test_anthropic.py#L625-L638) uses `any` to force tools. The log-detail UI displays the choice but does not declare invalid OpenAI values supported.
- **Maintainer ruling:** none found accepting `any` on OpenAI egress. [Shared-schema marshalling changes](https://github.com/maximhq/bifrost/commit/0e58cb323fd4e5510085ef6b4feab15c9170bc12) retain provider-generic enums; they do not normalize this OpenAI boundary.
- **Supported usage:** the built-in OpenAI setup and [Chat-only custom-provider configuration](https://github.com/maximhq/bifrost/blob/03ab391865710462302bbcf52dca2f32682b91b5/docs/providers/custom-providers.mdx#L87-L120) are documented. The keyless local gateway is a capture fixture, not a security precondition.
- **Boundary:** protocol translation, not disclosure. Valid Anthropic input becomes invalid OpenAI input and prevents a requested tool step.
- **Maintainer fix:** normalize generic forced-tool `any` to `required` when serializing OpenAI Responses and Chat requests without changing dialects that support `any`.

Classification label: `bug`.

## Upstream status

Checked 2026-09-06. The current stable OSS transport is `transports/v2.0.0`,
executed here. Current default branch `dev` at `03ab391865710462302bbcf52dca2f32682b91b5`
was source-inspected, not executed. The [core v1.8.4 release](https://github.com/maximhq/bifrost/releases/tag/core/v1.8.4)
contains unrelated reasoning/passthrough fixes.

Live GitHub searches included open/closed issues and PRs with `tool_choice any`,
`"tool_choice" "any" in:title,body`, `"forced tool"`, `"Invalid value" "any"`,
`ResponsesToolChoiceTypeAny`, and `"tool choice" in:title`. Relevant converter and
schema commits, release notes, changelogs and documentation were inspected.

No matching ticket was found. Related matches are [#4532](https://github.com/maximhq/bifrost/pull/4532)
(OpenRouter tool stripping), [#2309](https://github.com/maximhq/bifrost/pull/2309)
(later agent-turn choice reset), [#3315](https://github.com/maximhq/bifrost/pull/3315)
(Gemini ANY round-trip), and [#1787](https://github.com/maximhq/bifrost/pull/1787)
(empty object fields). None fixes this translation. Classification: `novel`,
meaning no match in these searches, not a proof that no discussion exists.

## Test

`anthropic_tool_choice_any_mapped_to_required` accepts only the promised bare
`"required"` string. It rejects malformed mode/type objects as well as `any`,
`auto`, null and absent choices. The sweep probe follows the same invariant.
Conformance tests read all five raw exchanges per file, check route and source
identity, raw/parsed consistency, status, provider error and SDK dispatch outcome.
Python tests also mutate a later trial, check exact one-field controls, verify
provenance rejection and test credential-safe capture behavior.

## Repro

Install the SDK dependencies in a virtual environment. The npm launcher version
is distinct from the transport version; explicitly pin the latter:

```sh
python3 -m pip install -r transcripts/072/requirements.txt
npx -y @maximhq/bifrost@1.6.3 --transport-version v2.0.0 --help

# macOS arm64 cache location; pass --bifrost-bin for another platform/location.
python3 -B transcripts/072/reproduce.py --output-dir /tmp/kairo-072-local-new
python3 -B transcripts/072/reproduce.py --live --env-file .env --output-dir /tmp/kairo-072-live-new

python3 -B -m unittest transcripts/072/test_reproduce.py
python3 -B -O -m unittest transcripts/072/test_reproduce.py
cargo test --workspace
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
python3 tools/update-readme-counts.py --check
```

Use fresh output directories; existing evidence is not overwritten. A failed run
keeps sanitized exchanges with `metadata.complete: false`. Live mode makes 40
bounded generation requests (five per condition), each limited to 100 output
tokens, and requires the supplied key to have `gpt-4o` access.
