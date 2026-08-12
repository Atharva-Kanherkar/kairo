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
| 004 | same on `/v1/responses`: 832-char `call_id` client must echo + phantom empty message item | LiteLLM 1.96.2 → Gemini 3 | — | ✅ repro on CURRENT (new finding) |
| 005 | Switchyard tool-id sanitizer non-injective: Kimi `functions.list_skills:0` → `functions_list_skills_0` | Switchyard 0.2.0 → Kimi | [Switchyard#178](https://github.com/NVIDIA-NeMo/Switchyard/issues/178) | ✅ corruption repro on CURRENT; multi-turn break backend-dependent |
| 012 | Anthropic image-in-tool_result forwarded to Gemini → hard 400 (portability cliff) | LiteLLM 1.96.2 → Gemini | — | ⚠️ repro (fails loud, not silent) |
| 003 | `[DONE]` in tool args (retried on token-streaming Kimi) | LiteLLM 1.96.2 → Kimi | litellm#31562 | ⬜ still not repro (patched) |
| 013 | parallel tool calls, Anthropic ingress | LiteLLM 1.96.2 → Gemini | — | ⬜ inconclusive (model returned 1 call under forced choice) |

**Tally**: 6 defects confirmed on the wire (5 on current releases) across
LiteLLM AND Switchyard, spanning 3 API dialects (Chat Completions, Anthropic
Messages, Responses) and 3 backends (Ollama, Gemini, Kimi). Two findings (the
id-smuggling in 004) are stronger than the tickets we started from; 005 is our
first Switchyard reproduction. Non-reproductions (003, 013, and the cited
symptoms of 001/004) are kept as data.

Method note: negatives are kept, not deleted. "Cited bug already patched" is
itself data — it is why a permanent regression suite matters, and every capture
here becomes one whether it currently passes or fails.
