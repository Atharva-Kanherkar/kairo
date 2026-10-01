# 090, agentgateway silently deletes a tool result's image on the OpenAI Chat Completions egress and returns HTTP 200

- **Upstream**: [agentgateway/agentgateway](https://github.com/agentgateway/agentgateway).
  No open or closed issue found on 2026-10-01 for a non-text `tool_result` part
  being deleted on the `Messages -> OpenAICompletions` hop. Searched the tracker
  for `tool_result image chat completions`, `non-text tool_result`, and
  `tool_result dropped`; the closest work is
  [#2689](https://github.com/agentgateway/agentgateway/issues/2689) ("Add
  Anthropic Messages to OpenAI Responses conversion", merged 2026-08-18), which
  covers the *other* egress and explicitly lists non-text tool results as
  fail-closed there.
- **Tool under test**: agentgateway `v1.5.0`, release binary
  `agentgateway-darwin-arm64`, `git_revision fe6732474a96a0363dfb9822859af4e9bab360fa`,
  `rust_version 1.98.0`. Independently reproduced on `v1.6.0-alpha.2`,
  `git_revision 02110e2ae82e37e72fd55702bcad19921c630218`. Egress format pinned
  by config, one bind per format, so the target dialect is never ambiguous.
- **Reproduced**: 2026-10-01, macOS arm64, keyless capture rig. **10 of 10** per
  binary, offline. Live consumer boundary against real OpenAI `gpt-4o-mini` the
  same day.
- **Label**: `bug`.

Source line numbers are for `v1.5.0`.

## What breaks

An agent drives a vision tool. The tool returns a screenshot inside a
`tool_result`. The client sends that to agentgateway on `/v1/messages`, which is
the Anthropic-native dialect. The selected provider only speaks OpenAI Chat
Completions, so agentgateway translates.

The translated request the provider receives no longer contains the image:

```json
{"role": "tool", "content": [{"type": "text", "text": "captured the viewport"}], "tool_call_id": "toolu_01"}
```

The client gets `HTTP 200` and the agent loop continues normally. Nothing is
logged. The model is now answering a question about a screenshot it has never
been shown, and the agent has no way to learn that.

The user affected is anyone running a vision or computer-use agent behind an
OpenAI-Chat-Completions-only upstream: screenshot tools, browser agents, image
generation review, OCR, document and chart readers. The failure is invisible at
every layer the agent can observe, because the tool genuinely ran and returned
success.

Two properties make this worse than a plain dropped field:

- The tool message survives, and its text sibling survives. So the wire looks
  well-formed and a reviewer reading the forwarded body sees a plausible tool
  result rather than an obvious hole.
- The loss is dialect-specific. The *same process*, on the *same request*, keeps
  the image on the Anthropic egress and, on `v1.6.0-alpha.2`, keeps it on the
  OpenAI Responses egress too. Only the Chat Completions hop deletes it, and only
  that hop answers `200`.

## Wire evidence

Client request, sent to agentgateway on `/v1/messages`, verbatim
(`transcripts/090/rig/req-toolresult-image.json`):

```json
{"role": "user", "content": [
  {"type": "tool_result", "tool_use_id": "toolu_01", "content": [
    {"type": "text", "text": "captured the viewport"},
    {"type": "image", "source": {"type": "base64", "media_type": "image/png",
      "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="}}
  ]}
]}
```

Forwarded to the OpenAI Chat Completions provider, recorded verbatim
(`transcripts/090/rig/forwarded-chat-body.json`):

```json
{"messages":[
  {"role":"user","content":[{"type":"text","text":"screenshot the page and tell me the total"}]},
  {"role":"assistant","tool_calls":[{"type":"function","id":"toolu_01","function":{"name":"browser_screenshot","arguments":"{}"}}]},
  {"role":"tool","content":[{"type":"text","text":"captured the viewport"}],"tool_call_id":"toolu_01"}],
 "model":"captured-model","max_completion_tokens":1024,"stream":false,
 "tools":[{"type":"function","function":{"name":"browser_screenshot","description":"capture a screenshot","parameters":{"type":"object","properties":{}}}}]}
```

Client status: `HTTP 200`. Gateway log: no warning, no error, no mention of the
image on any run.

Expected: either the media carried in the target dialect's own spelling, or a
refusal. Both are defensible; a `200` with the bytes deleted is not.

### Controls, same process, same request

| Egress (pinned by config) | Client status | Image in forwarded body |
|---|---|---|
| OpenAI Chat Completions | `200` | **no** |
| OpenAI Responses, `v1.5.0` | `400` | refused before egress |
| OpenAI Responses, `v1.6.0-alpha.2` | `502` (mock reply shape) | **yes**, as `input_image` |
| Anthropic Messages | `200` | **yes** |

The Anthropic row proves the gateway's request parser reads the image fine. The
Responses row proves the gateway can carry this exact part to an OpenAI-family
egress when it chooses to.

### Isolation, one variable at a time

| Variant, same Chat Completions route | Image survives |
|---|---|
| `tool_result` with text + image | no |
| `tool_result` with image only | no, forwarded as `"content": []` |
| `tool_result` with text + document | no |
| `tool_result` with `stream: true` | no |
| `tool_result` with text only | n/a, conformant |
| identical image in a **user turn** | **yes** |

The last row is the load-bearing control. The same base64 payload, the same
route, the same gateway, the same converter: it survives in a user turn and dies
in a `tool_result`. So the trigger is the position, not the image, not base64
handling, and not the OpenAI dialect generally.

### Live consumer boundary

The recorded forwarded body was replayed verbatim against real OpenAI
`gpt-4o-mini` on 2026-10-01:

```text
AS-FORWARDED (what agentgateway sent): HTTP 200
  "I have captured the screenshot of the page. If you need any further
   information or assistance, please let me know!"

IMAGE-ONLY variant, forwarded as "content": []: HTTP 200
  "I have captured the screenshot. However, it seems I am unable to access the
   specific content of the page ... my current environment does not support
   real-time data extraction or interpretation from images."
```

The model reports having seen a screenshot it never received, then explains the
absence as a limitation of its own environment. That is the user-visible
consequence: the agent does not crash, it confidently misdescribes its own
capabilities and returns a wrong answer. Measured, not inferred.

Direct control for the same live endpoint: restoring the image as an
`image_url` part on the tool message returns
`HTTP 400 invalid_request_error: "Image URLs are only allowed for messages with
role 'user', but this message with role 'tool' contains an image URL."`

That control is what forces the honest framing below. OpenAI Chat Completions
genuinely has no image part on a `tool` message, so this is **not** a claim that
the image should have been forwarded verbatim. The defect is the *silent
success*, not the absence of an equivalent.

## Root cause

`crates/llm/src/conversion/completions.rs:815-820`, the
`ToolResultContentPart` match inside `from_messages::translate_internal`:

```rust
ToolResultContentPart::Image { cache_control, .. }
| ToolResultContentPart::Document { cache_control, .. }
| ToolResultContentPart::SearchResult { cache_control, .. } => {
    if supports_prompt_cache_breakpoint {
        trailing_cache_control = trailing_cache_control.or(cache_control);
    }
},
```

The arm binds `cache_control` and discards `..`, which includes the part's
`source`. The only surviving effect is a cache breakpoint, so a media part
influences prompt caching while contributing nothing to the payload. The
adjacent text arm pushes a part; this one does not.

Two things a maintainer would notice on the same screen:

- The `messages::Tool::Server` arm a few lines below, dropping the same class of
  unsupported tool, calls `tracing::warn!("Unsupported server tool in
  completions conversion: ...")`. The media arm drops silently. That asymmetry
  is inside one function.
- `crates/llm/src/conversion/responses.rs:697-702` handles the identical part
  types and returns
  `unsupported("messages non-text tool_result content cannot be represented by
  responses")`. So the codebase already contains the fail-closed behaviour this
  path should have used, and #2689 documents it as the intended contract for the
  sibling hop.

Identical at `v1.6.0-alpha.2`.

## Bug or not

- **Is the expected behaviour really the spec?** Yes, and by the maintainers' own
  written contract. #2689 lists, under "Unsupported or non-equivalent Messages
  features fail closed before the upstream request is sent", the item
  "non-text tool results". That sentence is the spec for the Responses hop and
  it is the correct spec here too. The same PR's wording "a common agent subset
  rather than a full-fidelity universal protocol bridge" describes scoping the
  supported feature set, not licensing silent deletion of a requested one.
- **Have maintainers already ruled on it?** No. No ticket covers this hop. #2689
  closed the Responses edge, which fails loudly. This is the unaddressed
  sibling, not a deliberate classification of the Chat edge.
- **Is the trigger supported usage?** Yes. Default config, documented setup: an
  Anthropic client with an OpenAI-Chat-Completions-only provider is the exact
  scenario agentgateway's conversion table exists to serve
  (`chat(InputFormat::Messages, messages_fallback)`).
- **Is a real boundary crossed?** No data crosses a security boundary. The
  boundary crossed is the correctness contract: the caller is told the tool
  result was delivered when the payload was not. A vision tool's entire output
  channel is the image.
- **What fix would a maintainer ship?** Return the existing
  `unsupported("messages non-text tool_result content cannot be represented by
  completions")` for these parts, matching the Responses converter, instead of
  falling through to a `200` with the media deleted. One function, one error
  string that already exists in the codebase.

Label: `bug`.

## Test

`tool_result_media_not_silently_dropped` in
[`crates/harness/src/checks.rs`](../../crates/harness/src/checks.rs), asserted in
[`crates/harness/tests/conformance.rs`](../../crates/harness/tests/conformance.rs).
The invariant is a request-side form of the one this repo already enforces on
responses: a translator must map a field, refuse it, or say it dropped it. It
compares the client's request body with the forwarded body, so it scores any
gateway, not just this bug. It is deliberately shape-based rather than
spelling-based, so a gateway that carries the image as `image_url` on a `tool`
message, as `input_image` inside `function_call_output`, or as `image` inside
`tool_result` all pass.

Four tests: the reproduced violation, the Anthropic-egress control, the
user-turn control, and a text-only control asserting the checker stays quiet when
there was nothing to lose.

## Reproduce

Cold start, no credential, offline. Downloads nothing but the gateway binary:

```bash
cd transcripts/090/rig
chmod +x reproduce.sh
curl -sL -o agentgateway https://github.com/agentgateway/agentgateway/releases/download/v1.5.0/agentgateway-darwin-arm64
chmod +x agentgateway
./reproduce.sh ./agentgateway
```

Every rung prints `PASS` or `FAIL` and the script exits non-zero if the claim
does not hold. Verified `REPRODUCED` on `v1.5.0` (`fe673247`) and on
`v1.6.0-alpha.2` (`02110e2a`).

The script refuses to start if any of its ports is already bound, and asserts
that the listener answering `:4100` is the process it launched. This is not
theoretical: an earlier draft of this script ran against a stale gateway left
over from a previous session, silently tested that older binary, and produced
contradictory results across runs. A readiness probe cannot catch it, because
the stale process answers on the same port. The port guard and the pid
comparison are what make the run reproducible.

## What was not verified

- The exact behaviour against a real OpenAI Chat Completions provider *without*
  the gateway is not a fair control here, because the gateway's own output is
  what the provider received; the live replay above is the equivalent test.
- No other gateway was measured on this exact path in this run.
- Whether the maintainers prefer refusing the request or restructuring the tool
  message to carry the image as a user turn is a design decision, not something
  this reproduction settles.
- Upstream status was checked on 2026-10-01 only.