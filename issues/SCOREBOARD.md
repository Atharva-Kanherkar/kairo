# Reproduction scoreboard

Live captures, 2026-08-12. "Repro" = confirmed with our own recorded wire
bytes on the stated version. Each folder has a writeup + transcripts.

| # | Failure | Tool / version | Cited issue | Result |
|---|---------|----------------|-------------|--------|
| 001 | tool call arrives but `stop_reason`=`end_turn`; phantom empty block | LiteLLM 1.82.0 | [litellm#25390](https://github.com/BerriAI/litellm/issues/25390) | ✅ repro on 1.82.0; fixed (silently) on 1.96.2 |
| 001 | empty `tool_use.input` (the cited symptom) | LiteLLM 1.96.2 | litellm#25390/#29491 | ⬜ not repro on current (patched) |
| 002 | streaming `finish_reason`=`stop` not `tool_calls` | LiteLLM 1.96.2 → Ollama | [litellm#35663](https://github.com/BerriAI/litellm/issues/35663) | ✅ repro on CURRENT |
| 002 | `ollama/` route drops tool call entirely (→ empty msg / `reasoning_content`) | LiteLLM 1.96.2 → Ollama | litellm#31911 family | ✅ repro on CURRENT (worse than cited) |
| 003 | tool arg containing `[DONE]` false-terminates stream | LiteLLM 1.96.2 → Gemini | [litellm#31562](https://github.com/BerriAI/litellm/issues/31562) | ⬜ not repro (Gemini sends args whole; needs token-streaming backend) |
| 004 | `thought_signature` dropped → 400 | LiteLLM 1.96.2 → Gemini 3 | [ollama#14567](https://github.com/ollama/ollama/issues/14567) | ⬜ not dropped by LiteLLM (it's an Ollama/CCR bug) |
| 004 | signature smuggled into tool-call **id** → 382-char id w/ `+ / =`, violates OpenAI+Anthropic id charset | LiteLLM 1.96.2 → Gemini 3 | Switchyard#178 family | ✅ repro on CURRENT (new finding) |
| 004 | same on `/v1/responses`: 832-char `call_id` client must echo + phantom empty message item | LiteLLM 1.96.2 → Gemini 3 |, | ✅ repro on CURRENT (new finding) |
| 005 | Switchyard tool-id sanitizer non-injective: Kimi `functions.list_skills:0` → `functions_list_skills_0` | Switchyard 0.2.0 → Kimi | [Switchyard#178](https://github.com/NVIDIA-NeMo/Switchyard/issues/178) | ✅ corruption repro on CURRENT; multi-turn break backend-dependent |
| 012 | Anthropic image-in-tool_result forwarded to Gemini → hard 400 (portability cliff) | LiteLLM 1.96.2 → Gemini |, | ⚠️ repro (fails loud, not silent) |
| 003 | `[DONE]` in tool args (retried on token-streaming Kimi) | LiteLLM 1.96.2 → Kimi | litellm#31562 | ⬜ still not repro (patched) |
| 013 | parallel tool calls, Anthropic ingress | LiteLLM 1.96.2 → Gemini |, | ⬜ inconclusive (model returned 1 call under forced choice) |
| 006 | Switchyard silently drops `is_error`, flattens system + drops `cache_control`, invents `max_tokens=64000`, drops `n>1` | Switchyard 0.2.0 (capture rig) | source-audit paths | ✅ 4 losses repro offline on CURRENT |
| 007 | Switchyard stringifies image/document blocks in tool_result → model can't see them | Switchyard 0.2.0 (capture rig) |, | ✅ repro offline on CURRENT (browser/vision agents silently broken) |
| 007 | Switchyard streaming tool-call translation (OpenAI→Anthropic split-delta) | Switchyard 0.2.0 |, | ⬜ CORRECT, honest negative, their streaming works |
| 008 | LiteLLM `/v1/messages` crashes with unhandled IndexError → 500 "list index out of range" on >64-char tool name | LiteLLM 1.96.2 → Gemini | [litellm#17904](https://github.com/BerriAI/litellm/issues/17904) family | ✅ 5/5 on CURRENT; source line pinned (transformation.py:1326) |
| 009 | LiteLLM `/v1/responses` emits phantom empty `message` item (text:null) on every tool call | LiteLLM 1.96.2 → Gemini 3 |, | ✅ 3/3 on CURRENT (new finding) |
| 010A | Switchyard maps upstream `content_filter` finish to Anthropic `end_turn` (safety signal erased) | Switchyard 0.2.0 (rig) | [Switchyard#369](https://github.com/NVIDIA-NeMo/Switchyard/issues/369) | reproduced offline on CURRENT |
| 010B | Switchyard reorders reasoning/text when both are in one chunk (text emitted before thinking) | Switchyard 0.2.0 (rig) | [Switchyard#242](https://github.com/NVIDIA-NeMo/Switchyard/issues/242) | reproduced offline on CURRENT |
| P2/P4/P5/P7 | Gemini stream finish_reason; anyOf schema; multi-turn ordering; parallel ids | LiteLLM 1.96.2 → Gemini | #21041/#23870/#26755 | ⬜ not repro (patched / model-dependent), kept as data |
| 016 | thinking history destroyed on Anthropic request translate (dropped vs leaked as `output_text`) | Switchyard 0.2.0 + LiteLLM 1.96.2 (capture rig) |, | ✅ both on CURRENT; LiteLLM also routes `/v1/messages` through `/v1/responses` |
| 017 | `disable_parallel_tool_use` dropped (no `parallel_tool_calls: false`) | Switchyard 0.2.0 + LiteLLM 1.96.2 (capture rig) |, | ✅ both on CURRENT; LiteLLM same-format OpenAI chat is the control (keeps the flag) |
| 018 | user `document` block JSON-dumped (Switchyard) or deleted (LiteLLM) | Switchyard 0.2.0 + LiteLLM 1.96.2 (capture rig) | 007 family | ✅ both on CURRENT |
| 019 | Switchyard invents `cache_control: ephemeral` on every Anthropic-backend request | Switchyard 0.2.0 (capture rig) | `enable_anthropic_prompt_caching` | ✅ repro offline on CURRENT |
| 006 | LiteLLM `/v1/messages` → Responses also drops `is_error` | LiteLLM 1.96.2 (capture rig) | 006 family | ✅ same loss as Switchyard, now on LiteLLM |
| 007 | LiteLLM `/v1/messages` → Responses **deletes** image bytes in tool_result | LiteLLM 1.96.2 (capture rig) | 007 family | ✅ worse than Switchyard stringify: payload gone |
| 020 | Client JSON `api_key` replaces the deployment key without `allow_client_side_credentials`; router upserts it so later callers can inherit it | LiteLLM 1.96.2 (mock + live Gemini) | Huntr 4001e1a2 leftover (`api_key` not banned) | ✅ override 5/5 mock and 5/5 live; sticky 4/5 on mock. No real keys leaked. Header `x-goog-api-key` not forwarded on default config |
| 023 | Switchyard forwards client `api-key` and `OpenAI-Organization` / `OpenAI-Project`; those names are missing from `RESERVED_HEADERS` (`x-api-key` and `Authorization` are stripped) | Switchyard 0.2.0 (capture rig + live OpenAI) | incomplete reserved list | ✅ mock forward 5/5; live OpenAI invalid org 401 `mismatched_organization` 5/5. Live Gemini/OpenAI ignore `api-key` (200) so that leak is the header leaving the proxy. No real keys leaked |
| 024 | LiteLLM `GET /health` returns deployment `extra_headers` and `aws_session_token` in full. `api_key` is stripped and `api_base` is admin-only; those two fields are not | LiteLLM 1.96.2 (mock extra_headers + live Gemini key) | [litellm#36898](https://github.com/BerriAI/litellm/issues/36898) | ✅ canary 5/5. Live: full `GEMINI_API_KEY` on `/health` 5/5, first-4/last-4 on `/model/info` 5/5, live Gemini chat 200 with no key. Rotate Gemini. |
| 025 | Switchyard transport 502 copies reqwest's URL into `error.message`, including `base_url ?key=` credentials. Header Bearer keys and HTTP userinfo are not echoed | Switchyard 0.2.0 (capture rig + live OpenAI/Gemini/Anthropic/OpenRouter) | [Switchyard#423](https://github.com/NVIDIA-NeMo/Switchyard/issues/423) | ✅ canary 502 5/5. Live header-auth chats 200 5/5 (no key in body). Live `?key=` 502 contains the full Gemini/OpenAI/Anthropic/OpenRouter keys 5/5 each. Rotate all four. |
| 026 | LiteLLM honors JSON `extra_headers` / `headers` / `organization` without `forward_client_headers_to_llm_api`. Authorization in those fields replaces the deployment Bearer on the OpenAI path; `organization` becomes `OpenAI-Organization`; a 401 then cools the deployment so later callers get 429 | LiteLLM 1.96.2 (mock + live OpenAI/Gemini) | Huntr 4001e1a2 leftover (`extra_headers`/`organization` not banned) | ✅ mock 5/5. Live OpenAI invalid `organization` 401 2/2 then cooldown 429. Live Gemini `extra_headers.Authorization` 401 dual-auth 2/2 then cooldown 429. Direct OpenAI invalid org 401 5/5. No real keys leaked. Closed-port errors do not echo `?key=` |
| 027 | Switchyard forwards client `x-goog-api-key`; that name is missing from `RESERVED_HEADERS` (`x-api-key` and `Authorization` are stripped). JSON `api_key`/`organization`/`extra_headers` stay in the body and do not swap Authorization | Switchyard 0.2.0 (capture rig) | [Switchyard#410](https://github.com/NVIDIA-NeMo/Switchyard/issues/410) | ✅ mock forward 5/5. Authorization stays the deployment Bearer. Live Gemini prefers Bearer (021/023 pass, HTTP 200). The leak is the header leaving the proxy |
| 028 | LiteLLM `/gemini` pass-through injects env `GEMINI_API_KEY` as `?key=` and copies Google `x-goog-upload-url` / `x-goog-upload-control-url` to the caller. `x-pass-` forwards resumable-start headers that `forward_headers=false` would drop | LiteLLM 1.96.2 (capture mock + live Google) | pass-through header copy; `x-litellm-model-api-base` already strips `?` | ✅ mock canary 5/5. Live full `GEMINI_API_KEY` in both upload URLs 5/5. Direct Google same 5/5. Plain upload, chat, and closed-port 500 stay clean. Rotate Gemini. |
| 030 | Bifrost `/anthropic/v1/messages` **streaming** ends a text + tool-call turn with `stop_reason: end_turn` while emitting a valid `tool_use` block. Non-streaming Anthropic, and both OpenAI-route modes, are correct | Bifrost 1.6.11 (capture mock, no keys) | [bifrost#6123](https://github.com/maximhq/bifrost/issues/6123), regression of #3638 (fixed by #3640 in 1.5.4) | ✅ 5/5 on CURRENT; 3 control cells 5/5 conformant. Caught by the bug-001 checker unchanged. Repro went via the Responses upstream path (see writeup) |
| 031 | Bifrost drops `disable_parallel_tool_use` on the Anthropic ingress; forwarded `tool_choice` is bare `"auto"` with no `parallel_tool_calls` | Bifrost 1.6.11 (capture rig, no keys) | 017 family, third gateway | ✅ 5/5 on CURRENT. Control: same gateway's OpenAI route forwards `parallel_tool_calls: false` 5/5. Caught by the 017 checker unchanged |
| 032 | Bifrost drops `stop_sequences` on the Anthropic ingress; forwarded body has no stop field at all | Bifrost 1.6.11 (capture rig, no keys) | new, 006 family | ✅ 5/5 on CURRENT. Control: same gateway's OpenAI route forwards `stop` 5/5, so a mapping exists and is simply not applied |
| 033 | Bifrost drops assistant `thinking` blocks + signature from replayed history; only the visible text survives | Bifrost 1.6.11 (capture rig, no keys) | 016 family; adjacent [bifrost#5274](https://github.com/maximhq/bifrost/issues/5274) | ✅ 5/5 on CURRENT. Dropped, NOT leaked (unlike LiteLLM). Control: `is_error` → `status: incomplete` survives the same translator 5/5. Caught by the 016 checker unchanged |
| 034 | Bifrost reports upstream `content_filter` to Anthropic clients as `stop_reason: end_turn`, erasing the safety signal | Bifrost 1.6.11 (capture rig, no keys) | 010A family ([Switchyard#369](https://github.com/NVIDIA-NeMo/Switchyard/issues/369)), second gateway | ✅ 5/5 on CURRENT. Control: same gateway's OpenAI route reports `content_filter` 5/5. Caught by the 010A checker unchanged |
| 035 | Bifrost reports an upstream-truncated turn to Anthropic clients as `end_turn` instead of `max_tokens`; truncation is unreportable to the caller | Bifrost 1.6.11 (capture rig, no keys) | new; adjacent [bifrost#6081](https://github.com/maximhq/bifrost/issues/6081) | ✅ 5/5 on CURRENT. Both halves of the exchange frozen. Control: an untruncated turn carrying the SAME `end_turn` is conformant 5/5. Sibling of 034/036 |
| 036 | Bifrost delivers an upstream refusal to Anthropic clients as `content: []` with `end_turn`: both the refusal reason AND its text are erased | Bifrost 1.6.11 (capture rig, no keys) | new, 034/035 sibling | ✅ 5/5 on CURRENT. Control: an ordinary turn keeps `["text","tool_use"]` 5/5, so empty content is refusal-specific |
| 037 | Bifrost sanitizes a punctuation-bearing upstream tool-call id for the client (correctly) but forwards the sanitized form back upstream; the rewrite has no inverse | Bifrost 1.6.11 (capture rig, no keys) | 005 family (Switchyard sanitizer) | ✅ 5/5 on CURRENT. Sanitized id is charset-clean (asserted); the finding is the missing inverse. Live-provider rejection untested |
| 040 | Switchyard drops Anthropic `output_format` / json_schema on `/v1/messages`; OpenAI `response_format` on the same process is forwarded. Live Gemini: LiteLLM returns strict JSON, Switchyard returns a markdown fence | Switchyard 0.2.0 + LiteLLM 1.96.2 (capture + live Gemini) | 006/032 family | ✅ capture 5/5 both routes. Live Gemini 3/3. LiteLLM `/v1/messages` is the working control (`text.format json_schema`) |
| 041 | LiteLLM `/v1/messages` drops `stop_sequences` on the Responses hop (`model`/`input`/`max_output_tokens` only). Same process `/v1/chat/completions` and Switchyard `/v1/messages` both forward `stop` | LiteLLM 1.96.2 (capture) + Switchyard 0.2.0 + live Anthropic Haiku | 032 family, LiteLLM's own mapping docs | ✅ LiteLLM drop 5/5. LiteLLM OpenAI control 5/5. Switchyard Anthropic control 5/5. Direct Anthropic `stop_reason=stop_sequence` 3/3 |
| 042 | GoModel drops Anthropic `output_format` / json_schema on `/v1/messages`; OpenAI `response_format` on the same process is forwarded. Live Gemini returns a markdown fence | GoModel 0.1.77 (capture + live Gemini) | 040 family, third gateway | ✅ capture 5/5 both routes. Live Gemini 3/3 fenced/truncated. LiteLLM `/v1/messages` remains the working translation |
| 043 | GoModel drops `disable_parallel_tool_use` on the Anthropic ingress; forwarded `tool_choice` is bare `"auto"` with no `parallel_tool_calls` | GoModel 0.1.77 (capture rig, no keys) | 017/031 family, fourth gateway | ✅ 5/5 on CURRENT. Control: same gateway's OpenAI route forwards `parallel_tool_calls: false` 5/5. Caught by the 017 checker unchanged |
| 045 | Switchyard `/v1/messages` non-stream invents empty `{"type":"text","text":""}` before every `tool_use` when the backend is OpenAI-shaped (`content: null` + `tool_calls`). Stream and same-format Anthropic are clean | Switchyard 0.2.0 (capture mock + live Gemini 2.5 Flash) | sibling of 009 | ✅ canned 3/3, live Gemini 3/3. Control: Haiku same-format 3/3 and Gemini stream 3/3 |
| 051 | AxonHub drops Anthropic `output_format` / json_schema on `/v1/messages`; OpenAI `response_format` on the same process is forwarded | AxonHub v1.0.0-beta7 (capture rig, no keys) | 040/042 family, third gateway | ✅ capture 5/5 both routes. Caught by the 040 checker unchanged |
| 057 | any-llm Messages bridge drops assistant `thinking` blocks from replayed history; visible text survives | any-llm-sdk 1.26.0 (capture mock, no keys) | 016/033 family; Otari routes via this SDK | ✅ 5/5 on CURRENT. Dropped, not leaked (016 checker unchanged) |
| 058 | any-llm Messages bridge drops `disable_parallel_tool_use`; forwards bare `tool_choice: auto` | any-llm-sdk 1.26.0 (capture mock, no keys) | 017/043 family; adjacent any-llm#646 | ✅ 5/5 on CURRENT. Control: same SDK `completion()` keeps `parallel_tool_calls: false` 5/5 |
| 059 | any-llm Messages bridge drops `is_error` on tool results; plain string only | any-llm-sdk 1.26.0 (capture mock, no keys) | 006 family | ✅ 5/5 on CURRENT |
| 060 | any-llm Messages bridge drops image bytes inside tool results | any-llm-sdk 1.26.0 (capture mock, no keys) | 007 family; adjacent any-llm#1295 | ✅ 5/5 on CURRENT |
| 061 | any-llm Messages bridge drops document bytes inside tool results | any-llm-sdk 1.26.0 (capture mock, no keys) | 018 family | ✅ 5/5 on CURRENT. Control: user-content document route keeps DOCBODY 5/5 |
| 062 | any-llm accepts bare `{type, schema}` output_format, forwards empty `schema: {}` shell | any-llm-sdk 1.26.0 (capture mock, no keys) | 040 family, silent-loss variant | ✅ 5/5 on CURRENT. Control: documented `output_config.format` shape keeps `city` 5/5 |
| 063 | Switchyard follows HTTP 307 to another origin still holding Anthropic `x-api-key` and Gemini `extra_headers.x-goog-api-key`. OpenAI `Authorization` is stripped | Switchyard 0.2.0 (local 307 pair, live Anthropic / Gemini / OpenAI keys, transcripts redacted) | reqwest follows redirects; `forward_auth` already uses `Policy::none()` | ✅ Live Anthropic key on sink 5/5 (`api_key_env` and extra_headers). Live Gemini extra header on sink 5/5. Live OpenAI Bearer origin-only, stripped on sink 5/5. |
| 064 | LiteLLM `/v1/messages` drops `tools[].strict` while mapping Anthropic tools to OpenAI Responses; schema and name survive but the strict constraint does not | LiteLLM 1.96.2 (keyless capture rig) | adjacent [litellm#27490](https://github.com/BerriAI/litellm/issues/27490), reverse direction | ✅ capture 5/5, client HTTP 200 5/5. Same-proxy OpenAI ingress and direct Responses controls preserve `strict: true`. |
| 065 | Switchyard Responses to Chat drops array instructions and demotes inline `system` / `developer` input items to `user` | Switchyard main `053a61e` (keyless capture rig) | no matching upstream issue | ✅ capture 5/5, client HTTP 200 5/5. String-instructions control stays a Chat `system` message. |
| 066 | Switchyard `/v1/messages` drops Anthropic `tools[].strict` while translating to OpenAI Chat; schema and name survive but the strict constraint does not | Switchyard main `27fc1ce` (keyless capture rig) | 064 family, distinct gateway and target format | ✅ capture 5/5, client HTTP 200 5/5. Same-proxy OpenAI Chat ingress preserves `function.strict: true` 5/5. |
| 067 | LiteLLM `/v1/messages` erases structured refusal text while translating an OpenAI Responses refusal; client receives `content: []` with `end_turn` | LiteLLM 1.99.0 (keyless capture rig) | no matching upstream issue | ✅ capture 5/5, client HTTP 200 5/5. OpenAI Chat control preserves structured refusal 5/5. |
| 068 | Switchyard `/v1/messages` erases structured refusal text from an OpenAI Chat response and invents an empty Anthropic text block | Switchyard main `9523023`, 0.2.0 (keyless capture rig) | no matching upstream issue; distinct from 036 and 045 | ✅ capture 5/5, client HTTP 200 5/5. Same-proxy OpenAI response path preserves the same refusal 5/5. |

Numbers 046-050 are reserved for unpublished GoModel round-2 findings (one bug per PR). Issues 052-056 (AxonHub round 2) land on sibling branches, not missing rows here.

**Coverage**: 46 documented issue folders covering 50 distinct defects confirmed on the wire (49 on current releases)
across LiteLLM, Switchyard, Bifrost, GoModel, AxonHub, and any-llm, counting 006 as its 4 independent field losses
plus the LiteLLM copy of that class. LiteLLM confirmed: 001 (stop_reason, 1.82),
002a (finish_reason), 002b (route drop), 004a (id smuggle), 004b (Responses
call_id), 008 (IndexError crash), 009 (phantom message), 012 (image
portability), 016 (thinking leaked), 017 (parallel flag), 018 (document
deleted), 006/007 (is_error + image deleted via Responses), 020 (client
`api_key` override + sticky router upsert), 024 (`/health` extra_headers
and `aws_session_token` leak), 026 (JSON `extra_headers`/`headers`/`organization`
passthrough), 028 (`/gemini` pass-through copies `x-goog-upload-url ?key=`),
041 (`/v1/messages` drops `stop_sequences`), 064 (`/v1/messages` drops function-tool strictness), 067 (structured refusal text erased on Anthropic translation). Switchyard
confirmed: 005 (id sanitizer), 006 (4 field losses), 007 (multimodal
stringified), 016 (thinking dropped), 017 (parallel flag), 018 (document
dumped), 019 (invented cache breakpoint), 023 (`api-key` and OpenAI
org/project header forward), 025 (transport 502 echoes `?key=`), 027
(`x-goog-api-key` header forward), 040 (Anthropic `output_format` dropped), 045 (empty text block before non-stream `tool_use`), 063 (307 follow keeps `x-api-key` / `x-goog-api-key`), 066 (Anthropic function-tool `strict` dropped on the OpenAI Chat hop), 068 (structured refusal text erased and an empty Anthropic text block invented). Bifrost confirmed: 030 (Anthropic streaming
ends a tool-call turn as `end_turn`, a regression of their own fixed #3638,
caught by the bug-001 checker unchanged), 031 (parallel flag dropped), 032
(`stop_sequences` dropped), 033 (thinking history dropped), 034 (`content_filter`
erased), 035 (truncation erased), 036 (refusal content emptied), 037 (tool-id
sanitizer has no inverse). GoModel confirmed: 042 (Anthropic `output_format`
dropped), 043 (parallel flag dropped). AxonHub confirmed: 051 (Anthropic
`output_format` dropped). any-llm confirmed: 057 (thinking history dropped),
058 (parallel flag dropped), 059 (`is_error` dropped), 060 (image in tool
result dropped), 061 (document in tool result dropped), 062 (wrong
`output_format` shape forwards empty schema shell).

**any-llm honest negatives (2026-08-18 sweep)**, 5/5: Anthropic `stop_sequences`
maps to `stop` (unlike GoModel/Bifrost 032). Documented `output_config.format`
shape maps to a full `response_format.json_schema` (see 062 control); the bare
`{type, schema}` mistake forwards an empty shell instead. Otari inherits
any-llm's translation on **non-Anthropic backends** until fixed upstream.

**Bifrost honest negatives (2026-08-16 sweep)**, all 5/5 and kept as data because
they are the same probes that caught the other two gateways: client `Authorization`
/ `x-api-key` / `api-key` / `OpenAI-Organization` headers are NOT forwarded upstream
(unlike Switchyard 023); a client JSON `api_key` does NOT override the deployment
key (unlike LiteLLM 020); malformed bodies, a 300-char tool name, a `tool_result`
with no matching `tool_use`, and a negative `max_tokens` all return clean 4xx/200
with no stack trace, panic, or 500 (unlike LiteLLM 008); `is_error` on a tool result
IS carried (as `function_call_output.status`), and image and document bytes in
tool results ARE forwarded intact (unlike Switchyard 007/018 and LiteLLM's copies).
Bifrost's security surface and multimodal handling are genuinely better than both
incumbents; its Anthropic-ingress translation is worse.

Honest negatives kept: 003, 013
parallel-ids, P2/P4/P5/P7, and cited symptoms of 001/004. Several cited bugs
are genuinely patched on current, which is itself the argument for a
permanent regression suite. The 2026-08-13 capture-rig pass also showed
LiteLLM's Anthropic adapter forwarding `/v1/messages` through OpenAI
`/v1/responses` even when the configured backend is `openai/*`.
Spanning 3 API dialects (Chat Completions, Anthropic Messages, Responses) and 3
backends (Ollama, Gemini, Kimi). Findings stronger than their tickets: the
id-smuggling (004) and the offline capture-rig losses (006). Non-reproductions
(003, 013, cited symptoms of 001/004) are kept as data.

**Technique unlocked (006)**: pointing a translator's backend at a local
capture server exposes encode-side losses directly and offline, no keys, fully
deterministic, ideal for turning into replay tests.

Method note: negatives are kept, not deleted. "Cited bug already patched" is
itself data, it is why a permanent regression suite matters, and every capture
here becomes one whether it currently passes or fails.
