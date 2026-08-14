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
| 022 | JSON `extra_headers` / `headers` inject client secrets onto the upstream call and can replace provider auth, bypassing `forward_client_headers_to_llm_api` (default off) | LiteLLM 1.96.2 (mock + live Gemini) | Huntr 4001e1a2 leftover (`extra_headers`/`headers` not banned) | ✅ mock forward 5/5; live Gemini override 5/5 (`API_KEY_INVALID`). HTTP `x-goog-api-key` still dropped (200). Invalid extra_headers 401s the real deployment (later callers 429). No real keys leaked |

**Tally**: 19 distinct defects confirmed on the wire (18 on current releases)
across LiteLLM AND Switchyard, counting 006 as its 4 independent field losses
plus the LiteLLM copy of that class. LiteLLM confirmed: 001 (stop_reason, 1.82),
002a (finish_reason), 002b (route drop), 004a (id smuggle), 004b (Responses
call_id), 008 (IndexError crash), 009 (phantom message), 012 (image
portability), 016 (thinking leaked), 017 (parallel flag), 018 (document
deleted), 006/007 (is_error + image deleted via Responses), 020 (client
`api_key` override + sticky router upsert), 022 (JSON `extra_headers` /
`headers` leak and auth override). Switchyard
confirmed: 005 (id sanitizer), 006 (4 field losses), 007 (multimodal
stringified), 016 (thinking dropped), 017 (parallel flag), 018 (document
dumped), 019 (invented cache breakpoint). Honest negatives kept: 003, 013
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
