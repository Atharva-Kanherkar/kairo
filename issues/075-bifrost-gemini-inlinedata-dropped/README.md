# 075, Bifrost Gemini chat completions silently drops generated images

- **Upstream**: [maximhq/bifrost](https://github.com/maximhq/bifrost). Closely related open feature request [#2367](https://github.com/maximhq/bifrost/issues/2367) (OpenRouter `message.images`), not a duplicate; see Upstream status.
- **Tool under test**: `dev` branch, commit `e1045c07eb81bab18df2243f3f421f48450848ff`, built from source with `go1.26.5` (see Repro).
- **Reproduced**: 2026-09-07/08, macOS arm64, `gemini-2.5-flash-image`, real Gemini API key.
- **Review state**: self-reviewed and rerun successfully; separate independent review remains required before approval. Self-review is not independent approval.

## What breaks

An OpenAI-SDK-shaped client that asks Gemini's image-generation model
`gemini-2.5-flash-image` to draw a picture, through Bifrost's
`/v1/chat/completions` endpoint, gets back HTTP 200 with a caption and no
image. Gemini generated the image and Bifrost billed for it
(`usage.completion_tokens_details.image_tokens` is 1290 on every run), but
the base64 PNG bytes never leave Bifrost. There is no error, no truncation
flag, nothing that tells the caller an image existed. The same defect
reproduces in the streaming path: the chunk whose raw Gemini payload carries
the `inlineData` part is emitted with a delta containing only `role`, no
content at all.

```text
Client (OpenAI SDK) -> Bifrost /v1/chat/completions -> Gemini generateContent
   "draw an apple"        forwarded as-is              returns text + inlineData(image/png)
        <- HTTP 200, message.content = "" (or caption text only), image gone
```

The same Bifrost binary, same model, same prompt, through `/v1/responses`
instead, returns the image correctly as an `input_image` content block with
a `data:image/png;base64,...` URL. A direct call to Gemini's own
`generateContent` API, bypassing Bifrost entirely, also returns the image.
Only the chat-completions dialect converter loses it.

## Wire evidence

All paths below are under `transcripts/075/live/`. Each JSONL line is one
complete run; `client_request.body_raw` and `client_response.body_raw` are
literal wire text. Bifrost's own `send_back_raw_request` /
`send_back_raw_response` provider options embed Gemini's exact forwarded
request and response bytes inside `client_response.body_raw` (under
`extra_fields.raw_request` / `extra_fields.raw_response`), so no separate
capture relay was needed to get forwarded/upstream wire evidence: it is the
same HTTP exchange the client received, not a reconstructed side channel.

| File | Case | Result | Reproduction rate |
|---|---|---|---|
| `chat-nonstream.jsonl` | `/v1/chat/completions`, non-streaming | image dropped | 3/3 |
| `chat-stream.jsonl` | `/v1/chat/completions`, `stream:true` | image dropped from delta | 3/3 |
| `responses-control.jsonl` | `/v1/responses`, same Bifrost, same model | image present | 3/3 |
| `direct-gemini-control.jsonl` | direct `generateContent`, bypasses Bifrost | image present | 3/3 |

Base64 image payloads are roughly 1.2-1.5 MB each. To keep the repository a
reasonable size, every base64 blob longer than 500 characters is truncated
in the stored transcript to a short prefix/suffix plus the sha256 and exact
byte length of the untruncated value (see `transcripts/075/reproduce.py`,
`truncate_long_strings`). This is applied identically to every captured
field, so `body_raw` stays valid JSON/SSE text and the presence, MIME type,
and length of the image data are still provable without the repo carrying
megabytes of PNG bytes. Re-running `reproduce.py` reproduces the full
untruncated bytes locally.

Gemini's image generation is not fully deterministic: it occasionally
answers with text only. `reproduce.py` treats that as a precondition
failure and retries the same call (bounded, logged) until Gemini actually
generates an image for that trial, exactly as it would retry any other
flaky-provider-response precondition. This is separate from, and does not
affect, the 3/3 reproduction rate of the actual defect once an image was
generated: every trial in which Gemini returned an `inlineData` part had
that part dropped from the chat completions response.

### Example (one run, `chat-nonstream.jsonl`, image data elided further here)

Gemini's raw response (from `extra_fields.raw_response`):

```json
{"candidates":[{"content":{"parts":[
  {"inlineData":{"data":"iVBORw0KG...(1.3 MB)...","mimeType":"image/png"}}
],"role":"model"},"finishReason":"STOP","index":0}],
 "usageMetadata":{"candidatesTokenCount":1290,
   "candidatesTokensDetails":[{"modality":"IMAGE","tokenCount":1290}], ...}}
```

Bifrost's chat completions response to the same call:

```json
{"choices":[{"index":0,"finish_reason":"stop",
  "message":{"role":"assistant","content":""}}],
 "usage":{"completion_tokens":1290,
   "completion_tokens_details":{"image_tokens":1290}}}
```

`usage.completion_tokens_details.image_tokens` proves the image existed and
was billed. `message.content` proves it never reached the client.

### Credential handling

Only `GEMINI_API_KEY` is read, from the process environment or `.env`. It is
never written to a transcript: Bifrost receives it as a header, not inside
the JSON body, and `reproduce.py` refuses to persist any captured value that
contains the key string. Every transcript file was grepped for the key
value before being committed; none contain it.

## Root cause

`core/providers/gemini/chat.go` has two functions that turn a Gemini
`GenerateContentResponse` into a Bifrost chat-completions response:
`ToBifrostChatResponse` (unary) and `ToBifrostChatCompletionStream`
(streaming). Both iterate `candidate.Content.Parts` and handle `part.Text`,
`part.FunctionCall`, `part.FunctionResponse`, `part.CodeExecutionResult`,
and `part.ExecutableCode`. Neither has a case for `part.InlineData`, the
field Gemini uses to carry a base64 image or audio blob
(`core/providers/gemini/types.go:1667`,
`InlineData *Blob \`json:"inlineData,omitempty"\``). A part with only
`InlineData` set falls through every `if`/`switch` arm untouched and is
silently dropped.

- Unary loop with no `InlineData` handling: `core/providers/gemini/chat.go:169-291` (`ToBifrostChatResponse`).
- Streaming loop with no `InlineData` handling: `core/providers/gemini/chat.go:431-526` (`ToBifrostChatCompletionStream`, the `switch` at line 432).

Bifrost's own schema already has the vocabulary for this:
`ChatContentBlockTypeImage` (`"image_url"`) and
`ChatContentBlockTypeInputAudio` (`"input_audio"`) at
`core/schemas/chatcompletions.go:1133-1134`. The sibling Responses API
converter uses exactly this pattern for the same Gemini `InlineData` field,
building an `image_url`/`output_image`-shaped block at
`core/providers/gemini/responses.go:2528-2554` (unary) and
`core/providers/gemini/responses.go:1371-1373` /
`core/providers/gemini/responses.go:1828-1856` (streaming). The dedicated
image-generation converter `core/providers/gemini/images.go:284-286` and
`:368-376` also reads `part.InlineData` correctly. Chat completions is the
one converter that never wires this up, on either the unary or the
streaming path.

## Bug or not

- **Expected behavior is the spec:** OpenAI's chat completions content
  schema documents `image_url` and `input_audio` content block types for
  assistant messages, and Bifrost's own schema (`ChatContentBlockTypeImage`,
  `ChatContentBlockTypeInputAudio`) already models them. Bifrost's own
  Responses API converter and dedicated image converter both already
  populate these fields from the same Gemini `InlineData` value, so this is
  Bifrost's own established behavior for the same input, not a stale
  external doc line.
- **Maintainer ruling:** no commit, PR, or comment found that classifies
  dropping `InlineData` in chat completions as intentional. PR
  [#1265](https://github.com/maximhq/bifrost/pull/1265) ("missing image url
  and audio struct in chat completion gemini", merged 2026-01-07) added
  image/audio handling to `chat.go`, but only on the request-building side
  (`ToGeminiChatCompletionRequest` / `convertBifrostMessagesToGemini`,
  converting a caller's input images into Gemini `Parts`), not the response
  side this finding is about. No ruling excludes the response direction.
- **Trigger is supported usage:** `/v1/chat/completions` with a Gemini
  image-generation model and a plain text prompt is default, documented
  usage; no special flag or unsupported provider layout is needed.
- **Boundary:** not a disclosure issue. This is silent data loss: the
  response the model produced is not the response the client receives, with
  no signal that anything is missing.
- **Fix a maintainer would ship:** add an `InlineData` case to both
  `ToBifrostChatResponse` and `ToBifrostChatCompletionStream` that builds a
  `ChatContentBlock{Type: ChatContentBlockTypeImage or ChatContentBlockTypeInputAudio, ...}`
  from `part.InlineData`, following the same MIME-type branch already
  written in `responses.go`.

Classification label: `bug`.

## Usefulness

- **Affected user:** any developer using an OpenAI-SDK-compatible client
  (or Bifrost's own OpenAI-compatible integration layer) to call a Gemini
  image-generation model through Bifrost's native `gemini` provider via
  `/v1/chat/completions`, in both non-streaming and streaming modes.
- **Real workflow:** "generate an image from a prompt" is the documented
  purpose of `gemini-2.5-flash-image`. A chat app, image-generation bot, or
  agent tool built against the OpenAI chat completions shape (the most
  common one SDKs target) gets a 200 response with billed image tokens and
  no way to retrieve the image. There is no error path to catch; the
  failure is indistinguishable from "the model declined silently" unless
  the caller cross-checks `usage.completion_tokens_details.image_tokens`
  against `message.content`, which nothing prompts them to do.
- **Chain:** user asks for an image -> Gemini generates it and Bifrost's own
  raw-response capture proves it arrived -> `ToBifrostChatResponse` /
  `ToBifrostChatCompletionStream` drop the `InlineData` part -> client
  receives HTTP 200 with text-only or empty content and billed tokens for
  an image it never sees.
- **Frequency:** measured at 3/3 for both the unary and the streaming path,
  every time Gemini actually generated an image in that call. This is not
  an edge case; it is the normal outcome of asking this model class for an
  image through this route.

## Upstream status

Checked 2026-09-08 against `github.com/maximhq/bifrost`, default branch
`dev` at commit `e1045c07eb81bab18df2243f3f421f48450848ff`.

Search terms used (via `gh search issues` / `gh search prs`): `inlineData`,
`flash-image`, `gemini image generation chat completions`, `chat
completions image dropped`, `ToBifrostChatResponse inlineData`, and manual
review of every Gemini-tagged issue/PR touching `core/providers/gemini/`.

Relevant matches:

- [#2367](https://github.com/maximhq/bifrost/issues/2367) (open, filed
  2026-03-29, labeled `feature`): "Support for message.images array from
  OpenRouter image generation models (Gemini Nano Banana / gemini-*-image)".
  Same symptom class (image generated, `image_tokens` billed, dropped from
  `message.content`) and the same evidence pattern (~1290 image tokens), but
  a **different provider path**: it is filed against Bifrost's
  `openrouter` provider forwarding to OpenRouter's API, which returns the
  image in OpenRouter's own proprietary `message.images` field, not
  Gemini's `inlineData`. That is a different wire shape, a different
  provider implementation, and the issue asks for a new pass-through
  feature rather than reporting that Bifrost's own `gemini` provider drops
  a field it already knows how to convert elsewhere. Closely related, but
  not the same file, function, or root cause as this finding, and it is
  filed as a feature request, not a bug.
- [#1265](https://github.com/maximhq/bifrost/pull/1265) (merged
  2026-01-07): added image/audio handling to `chat.go`, but only for the
  request-building direction (see Bug or not). Does not touch the response
  conversion this finding is about.
- [#5314](https://github.com/maximhq/bifrost/issues/5314) and
  [#5324](https://github.com/maximhq/bifrost/issues/5324) (both open,
  2026-07-17): both about the native `/genai/.../generateContent` ingress
  endpoint's own image-edit/aspect-ratio detection, unrelated route and
  direction.
- [#4202](https://github.com/maximhq/bifrost/pull/4202) and
  [#3571](https://github.com/maximhq/bifrost/pull/3571): about media
  attached to tool/function responses (`FunctionResponse.Parts`), not the
  model's own top-level generated content this finding is about.
- [#3289](https://github.com/maximhq/bifrost/issues/3289): image content
  blocks dropped on Bedrock's Converse ingest, a different provider and
  direction (request ingest, not Gemini response egress).

No issue or PR was found reporting that Bifrost's native `gemini` provider
drops `inlineData` parts from `/v1/chat/completions` on either the unary or
the streaming path. Classification: `novel`, with the above matches
recorded so a maintainer or future contributor can see exactly what was
checked and ruled out.

## Frozen invariant

- Issue writeup: this file.
- Checker added: `gemini_inline_media_preserved_in_chat_response` and
  `gemini_inline_media_preserved_in_chat_stream` in
  `crates/harness/src/checks.rs`.
- Conformance tests added: `bifrost_gemini_chat_completions_drops_inline_image`,
  `bifrost_gemini_chat_completions_stream_drops_inline_image`,
  `bifrost_gemini_responses_control_preserves_inline_image`,
  `bifrost_gemini_direct_control_returns_inline_image` in
  `crates/harness/tests/conformance.rs`.
- Why the checker tests the invariant rather than one implementation
  detail: it does not look for a specific Bifrost internal field. It takes
  Gemini's raw response (proof an image/audio blob was generated) and
  Bifrost's client-facing response, and asserts only that when the former
  has an `inlineData` part, the latter must have a matching `image_url` or
  `input_audio` content block somewhere in `message.content` (or
  `delta.content` for streaming). A fix that represents the image any other
  reasonable way, or a future response that legitimately carries no image,
  both pass; only silent loss of a real generated image fails.

## Validation

| Check | Command | Result |
|---|---|---|
| Reproduction | `python3 -B transcripts/075/reproduce.py --output-dir <dir> --env-file .env` | 3/3 chat (unary+stream) drop the image; 3/3 both controls preserve it |
| Harness | `cargo test --workspace` | see PR |
| Formatting | `cargo fmt --all -- --check` | see PR |
| Lint | `cargo clippy --workspace --all-targets -- -D warnings` | see PR |
| README counts | `python3 tools/update-readme-counts.py --check` | see PR |

## Repro

```sh
# Build Bifrost from the pinned commit (Go 1.26.1+; a placeholder ui/index.html
# is required locally to satisfy //go:embed all:ui without building the full
# Next.js admin UI, which is unrelated to this backend converter):
cd /path/to/bifrost && git checkout e1045c07eb81bab18df2243f3f421f48450848ff
mkdir -p transports/bifrost-http/ui && echo "<html></html>" > transports/bifrost-http/ui/index.html
cd transports/bifrost-http && GOWORK=off go build -o /tmp/bifrost-http .

# Reproduce against live Gemini (GEMINI_API_KEY from environment or .env):
python3 -B transcripts/075/reproduce.py \
  --output-dir /tmp/kairo-075-live-new \
  --bifrost-bin /tmp/bifrost-http \
  --env-file .env

cargo test --workspace
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
python3 tools/update-readme-counts.py --check
```

Use a fresh `--output-dir`; existing evidence is never overwritten. A failed
run keeps sanitized exchanges with `metadata.json`'s `complete` field left
`false`. Live mode makes real, billed Gemini image-generation calls; each
run needs `gemini-2.5-flash-image` access on the supplied key.
