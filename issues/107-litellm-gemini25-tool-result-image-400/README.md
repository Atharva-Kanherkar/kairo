# 107, LiteLLM nests tool-result images inside Gemini's `functionResponse` for every model, so Gemini 2.5 rejects the turn and the agent session cannot continue

- **Upstream**: [BerriAI/litellm](https://github.com/BerriAI/litellm). No issue or
  pull request reports this on 2026-10-22. Introduced by
  [#29325](https://github.com/BerriAI/litellm/pull/29325) ("Fix Gemini multimodal
  function response conversion", merged 2026-06-03), first released in `v1.89.0`
  (2026-06-14). `v1.88.2` sends the working shape. Classified `regression`.
  Related, different reports: [#35236](https://github.com/BerriAI/litellm/issues/35236)
  (open; on Vertex `gemini-3.6-flash` the old sibling shape fails, which is why the
  fix below gates by model instead of reverting) and
  [#23712](https://github.com/BerriAI/litellm/issues/23712) / [#23724](https://github.com/BerriAI/litellm/pull/23724)
  (images in tool results used to be dropped on `/v1/messages`, fixed in March 2026)
- **Tool under test**: LiteLLM proxy
  [`v1.106.2`](https://github.com/BerriAI/litellm/releases/tag/v1.106.2) at
  `49943cd52ea46aacf8db2e5cb2b890d580fe9bb4` (latest release, 2026-10-21), installed
  editable from the tag. Code unchanged on `main` at `f2118fc7` (2026-10-22).
  Controls: `litellm[proxy]==1.88.2` from PyPI (`a0d05ba2`, the last release before
  #29325), and `v1.106.2` with only [`rig/fix.diff`](../../transcripts/107/rig/fix.diff)
  applied
- **Provider**: live Gemini API (`generativelanguage.googleapis.com`, the `gemini/`
  provider), `gemini-2.5-pro`, `gemini-2.5-flash-lite`, `gemini-2.5-flash`; control
  model `gemini-3.8-flash`
- **Clients**: raw HTTP in three dialects (`/v1/chat/completions`, `/v1/messages`,
  `/v1/responses`), Claude Code `2.1.289` and Codex `0.159.3`
- **Reproduced**: 2026-10-22, macOS arm64, proxy with a master key and default
  settings, recording relay between LiteLLM and Google. Committed wire and direct
  bytes 03:17Z to 03:32Z (provider `Date` headers), Claude Code sessions 03:26Z to
  03:34Z, Codex runs about 03:34Z to 03:39Z
- **Label**: `bug`

## Claim

When a tool result carries an image, LiteLLM's Gemini translation
(`convert_to_gemini_tool_call_result`) puts the image inside
`function_response.parts` for every Gemini model. Google documents that shape as
supported "for Gemini 3 and later models", and the Gemini 2.5 models answer it with
`400 INVALID_ARGUMENT: Multimodal function responses are not supported for this
model.` Until `v1.89.0`, LiteLLM sent the image as a sibling `inline_data` part next
to the `function_response`, which Gemini 2.5 accepts and reads.

Clients replay the tool result on every later turn, so while an image tool result
stays in the history, every request in that session fails with the same 400.

## What breaks

A team runs Claude Code, Codex, or its own agent through LiteLLM on a Gemini 2.5
model from the Gemini API, which still lists 2.5 Pro, Flash and Flash-Lite as
generally available with "No shutdown date announced".

1. A tool returns an image: Claude Code's `Read` on a PNG or screenshot, Codex's
   `view_image`, a browser MCP screenshot, or any OpenAI-style `tool` message with
   an `image_url` part.
2. LiteLLM forwards
   `{"function_response": {"name": ..., "response": ..., "parts": [{"inline_data": ...}]}}`.
3. Gemini 2.5 returns 400. LiteLLM passes it through as `litellm.BadRequestError`.
4. Claude Code ends the run with `API Error: 400`. A text-only follow-up in the same
   session fails the same way, because the image tool result is still in the history.
   Codex reconnects five times and stops with `stream disconnected before completion`.

The same requests on `gemini-3.8-flash`, on `v1.88.2`, or on `v1.106.2` plus the
three-line gate all succeed and the model reads the image.

## Wire evidence

All under [`transcripts/107`](../../transcripts/107). Every wire cell holds the
client request and response bytes and the forwarded Gemini request and response,
recorded by [`rig/relay.py`](../../transcripts/107/rig/relay.py) with the
`x-goog-api-key` header value replaced by `[REDACTED]`.

Forwarded tool-result turn, `v1.106.2`, `/v1/messages`, `gemini-2.5-pro`
([`wire/messages-g25p-v1.106.2-r1/upstream/fwd-004-request.http`](../../transcripts/107/wire/messages-g25p-v1.106.2-r1/upstream/fwd-004-request.http)),
base64 shortened here:

```json
{"role": "user", "parts": [{"function_response": {"name": "read_image", "response": {"content": ""},
  "parts": [{"inline_data": {"data": "iVBORw0KGgo...", "mime_type": "image/png"}}]}}]}
```

```text
HTTP/1.1 400
{"error": {"code": 400, "message": "Multimodal function responses are not supported for this model.", "status": "INVALID_ARGUMENT"}}
```

Same client bytes through `v1.88.2`
([`wire/messages-g25p-v1.88.2-r1`](../../transcripts/107/wire/messages-g25p-v1.88.2-r1)):

```json
{"role": "user", "parts": [{"function_response": {"name": "read_image", "response": {"content": ""}}},
  {"inline_data": {"data": "iVBORw0KGgo...", "mime_type": "image/png"}}]}
```

```text
HTTP/1.1 200
{"candidates": [{"content": {"parts": [{"text": "ZEBRA-7342", ...
```

### Results

Raw wire matrix, [`wire/results.json`](../../transcripts/107/wire/results.json):
three dialects, three runs each, so 9 cells per row.

| Build | gemini-2.5-pro | gemini-2.5-flash-lite | gemini-3.8-flash |
|---|---|---|---|
| `v1.106.2` | 400, 9/9 | 400, 9/9 | 200 and image read, 9/9 |
| `v1.88.2` | 200 and image read, 9/9 | 200 and image read, 9/9 | 200 and image read, 9/9 |
| `v1.106.2` + `fix.diff` | 200 and image read, 9/9 | 200 and image read, 9/9 | 200 and image read, 9/9 |

Direct Gemini API control with no gateway,
[`direct/results.json`](../../transcripts/107/direct/results.json), three runs each:

| Model | Nested in `functionResponse.parts` | Sibling `inlineData` part |
|---|---|---|
| gemini-2.5-pro | 400, 3/3 | 200 and image read, 3/3 |
| gemini-2.5-flash-lite | 400, 3/3 | 200 and image read, 3/3 |
| gemini-3.8-flash | 200 and image read, 3/3 | 200 and image read, 3/3 |

Exploratory direct calls, one run each, not committed as bytes: `gemini-2.5-flash`
nested 400 and sibling 200; `gemini-3.1-flash-lite`, `gemini-3.5-flash`,
`gemini-3.5-flash-lite`, `gemini-3.6-flash`, `gemini-3.7-flash`, `gemini-4-argon`
and `gemma-4-31b-it` nested 200; `gemini-3.5-flash`, `gemini-3.6-flash`,
`gemini-3.7-flash` and `gemma-4-31b-it` sibling 200.

## Root cause

`litellm/litellm_core_utils/prompt_templates/factory.py:1600-1604` at `v1.106.2`:

```python
    # For multimodal function responses, Gemini expects media parts nested
    # inside functionResponse.parts instead of sibling content parts.
    if inline_data_list:
        _function_response["parts"] = [{"inline_data": inline_data} for inline_data in inline_data_list]
        return [_part]
```

Nothing checks the model. The caller already has the right gate in hand:
`litellm/llms/vertex_ai/gemini/transformation.py:719` computes
`forward_function_call_id` from `VertexGeminiConfig._is_gemini_3_or_newer`, whose
docstring (`vertex_and_google_ai_studio_gemini.py:309-315`) says "Older Gemini models
reject the field with HTTP 400" about `functionResponse.id`. Nested media parts are
the same kind of Gemini 3 only field and need the same gate.

## Consumer boundary

Claude Code `2.1.289`, prompt "Read the image file code.png with the Read tool, then
write the exact text it shows into answer.txt.", then a text-only follow-up in the
same session (`--resume`). Every run's session log shows `Read` returning an
`image` block. [`agents/claude-code/results.txt`](../../transcripts/107/agents/claude-code/results.txt):

| Build | Model | Image turn | Text-only follow-up |
|---|---|---|---|
| `v1.106.2` | gemini-2.5-pro | `API Error: 400`, 3/3 | `API Error: 400`, 3/3 |
| `v1.106.2` | gemini-2.5-flash-lite | `API Error: 400`, 3/3 | `API Error: 400`, 3/3 |
| `v1.106.2` | gemini-3.8-flash | `ZEBRA-7342` written, 3/3 | `OK` written, 3/3 |
| `v1.106.2` + `fix.diff` | gemini-2.5-pro | `ZEBRA-7342` written, 3/3 | `OK` written, 3/3 |
| `v1.106.2` + `fix.diff` | gemini-2.5-flash-lite | `ZEBRA-7342` written, 3/3 | `OK` written, 3/3 |
| `v1.106.2` + `fix.diff` | gemini-3.8-flash | `ZEBRA-7342` written, 3/3 | `OK` written, 3/3 |

Codex `0.159.3`, "Use your view_image tool to look at code.png, then write the exact
text it shows into answer.txt.", [`agents/codex/results.txt`](../../transcripts/107/agents/codex/results.txt):
`v1.106.2` with gemini-2.5-pro reconnected 5 times and stopped with the 400, 3/3;
`v1.106.2` + `fix.diff` wrote `ZEBRA-7342`, 3/3; `v1.106.2` with gemini-3.8-flash
wrote `ZEBRA-7342`, 3/3.

Measured: every image tool result on a Gemini 2.5 model fails the turn, and the
next turn of that session fails too while the image stays in the replayed history.
Not measured: recovery after the history is cleared or rewound, and how often
users hit image tools. The 400 is deterministic once the image tool runs, but
whether the agent chooses to call the tool varies (one exploratory Claude Code run
on Flash-Lite wrote an answer without calling `Read`). Inferred, not measured: the same applies to
PDF and other file blocks in tool results, which take the same `inline_data_list`
path, and to the Vertex `vertex_ai/` provider, which shares this code but was not
run (no GCP credentials; Google retired 2.5 on Vertex on 2026-10-20).

## Bug or not

- **Spec**: Google's function-calling docs, on both the Gemini API and Vertex,
  introduce multimodal function responses with "For Gemini 3 and later models", and
  the API enforces that (direct control above). LiteLLM's model map lists
  `gemini/gemini-2.5-pro`, `gemini/gemini-2.5-flash` and `gemini/gemini-2.5-flash-lite`
  with `supports_vision` and `supports_function_calling` true and no deprecation date.
  Before #29325, LiteLLM sent tool-result images as sibling parts (that PR's own
  description), and `v1.88.2` reads the image on Gemini 2.5 (tested above).
- **Maintainer ruling**: none. #29325 cites Google's docs for the nested shape but
  says nothing about older models, and no comment, test or denylist marks 2.5 as
  unsupported for image tool results. The same codebase gates the neighbouring
  `functionResponse.id` field on Gemini 3+ for exactly this reason.
- **Supported usage**: default proxy with a master key, a GA model the Gemini API
  still serves with no announced shutdown, and LiteLLM's own Claude Code setup
  (`/v1/messages`, "Use Claude Code with Non-Anthropic Models"). That tutorial's
  example model is `gemini-3.8-flash`, which is not affected.
- **Boundary crossed**: not a disclosure claim.
- **Fix a maintainer would ship**: nest tool-result media in
  `function_response.parts` only when `_is_gemini_3_or_newer(model)` is true, and send
  it as sibling `inline_data` parts otherwise, as `v1.88.2` did
  ([`rig/fix.diff`](../../transcripts/107/rig/fix.diff) reuses the
  `forward_function_call_id` flag, which is that same check).

## Upstream status

Checked 2026-10-22 against `v1.106.2` and `main` `f2118fc7`.

- Issue and PR searches on BerriAI/litellm: "Multimodal function responses",
  "multimodal function response", "functionResponse parts", "function_response parts",
  "functionResponse image", "tool result image gemini", "gemini 2.5 image tool",
  "gemini-2.5 tool_result image", "gemini 2.5 screenshot tool", "not supported for
  this model gemini", "gemini image tool result 400", "computer use gemini screenshot",
  "is_gemini_3_or_newer function_response", plus a broad listing of every Gemini issue
  opened since 2026-09-01. The only hits are #29325 (the change), #35236 (Vertex
  3.6, opposite direction) and #23712 / #23724 (an older, fixed drop).
- GitHub-wide issue search for the exact error string: no results.
- Code history: the nested shape is absent at `v1.88.2` and present from `v1.89.0`
  through `v1.106.2` and `main`.
- Google docs checked the same day: Gemini API function calling ("For Gemini 3 series
  models, you can include multimodal content in the function response parts"), Vertex
  function calling ("For Gemini 3 and later models ..."), Gemini API deprecations
  (2.5 Pro, Flash, Flash-Lite: "No shutdown date announced"), Vertex model versions
  (2.5 retired 2026-10-20).

Classification: `regression`.

## Test

- Checker `tool_result_images_reach_provider` in
  [`crates/harness/src/checks.rs`](../../crates/harness/src/checks.rs). Invariant: an
  image a client returns in a tool result reaches the provider, and the provider
  accepts the turn that carries it. It finds tool-result images in Chat, Anthropic
  and Responses request bodies, then counts only image parts (Gemini `inline_data`
  with an image MIME type, Chat `image_url`, Anthropic `image`, Responses
  `input_image`) in the forwarded tool-result turn, nested or beside the result, or
  in the user turn right after it. A data URL pasted into text or a JSON function
  response, or a matching image elsewhere in the history, does not count. It then
  judges by the provider's own status and names no gateway function. Unit tests
  `tool_result_images_count_wherever_the_tool_result_turn_carries_them`,
  `tool_result_images_not_delivered_as_image_parts_with_the_result_are_dropped` and
  `tool_result_image_checker_refuses_bad_and_unexercised_input` cover drops, the pasted
  data URL, JSON-dumped images, earlier-image masking, non-image MIME types, images
  outside the tool-result turn, rejections, remote URLs, duplicates, truncation,
  unknown dialects and non-JSON input.
- [`crates/harness/tests/conformance.rs`](../../crates/harness/tests/conformance.rs):
  `bug107_gemini_2_5_rejects_the_tool_result_media_litellm_forwards` runs the checker
  over all 81 wire cells, `bug107_direct_gemini_takes_sibling_media_on_2_5_and_both_shapes_on_3`
  pins the direct control, `bug107_claude_code_session_cannot_continue_after_an_image_read`
  pins the consumer matrix, and
  `bug107_image_pasted_as_text_into_the_function_response_is_not_delivery` runs the
  checker over three live captures of the #23712 shape
  ([`adversarial/`](../../transcripts/107/adversarial), from
  [`rig/adversarial_raw.py`](../../transcripts/107/rig/adversarial_raw.py)): Gemini
  2.5 Pro answers 200 and never reads the image (3/3), and the checker reports the
  drop. Against the first version of the checker, which matched the bytes anywhere
  in the body, the new unit test and this test both fail.

## Reproduce

[`transcripts/107/rig/reproduce.sh`](../../transcripts/107/rig/reproduce.sh) clones
`v1.106.2`, builds the three proxies, starts the recording relays and sends one
request per dialect and model to each build. It needs `uv`, `git`, `python3` and
`GEMINI_API_KEY` (or `KAIRO_ENV` pointing at a dotenv file that sets it). The agent
runs use [`rig/cc2.sh`](../../transcripts/107/rig/cc2.sh) and
[`rig/cximg.sh`](../../transcripts/107/rig/cximg.sh) against the unrecorded
[`rig/config.yaml`](../../transcripts/107/rig/config.yaml).

## Limits

- Vertex AI was not run. The code path is shared, but Google retired the 2.5 models
  on Vertex on 2026-10-20, so the live impact today is on the Gemini API.
- `gemini-2.5-flash` is in the exploratory results only. Through the `v1.88.2`
  control it read the image but often wrote its tool call as `tool_code` text instead
  of calling the tool, a known model quirk that makes it a noisy consumer control.
- `v1.88.2` was not used as the Claude Code or Codex control: with Claude Code on
  2.5 Pro it looped on "Output token limit hit", and with Codex it failed with
  "Missing corresponding tool call", both unrelated older defects. The patched
  `v1.106.2` is the consumer control instead.
- `gemini-2.5-computer-use-preview-10-2025` was not tested: it returned 503 "high
  demand" for both shapes, and Google lists it as shut down since 2026-07-28. The
  LiteLLM code comments name Computer Use screenshots as a use of this path, so a
  fix should confirm that model's behavior before gating it with the other 2.5
  models.
- Codex's five reconnects come from LiteLLM sending the 400 as an error inside an
  HTTP 200 stream. That is noted here, not claimed.
