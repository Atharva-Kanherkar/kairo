# Target issues, verified open, most-cited, cross-project

Reproduction order favors: still open, recurred multiple times, breaks real
agent loops, cheap to reproduce. States verified 2026-08-12; re-check before
each reproduction (`gh issue view <n> --repo <repo>`).

| # | Upstream | Failure | Repro needs |
|---|----------|---------|-------------|
| 001 | [litellm#25390](https://github.com/BerriAI/litellm/issues/25390), [litellm#29491](https://github.com/BerriAI/litellm/issues/29491) | streaming drops `tool_use.input` (regressed 5×: also 20711, 24134, 25321) | Gemini or OpenAI key + LiteLLM |
| 002 | [litellm#35663](https://github.com/BerriAI/litellm/issues/35663) | Ollama streaming: `finish_reason: stop` instead of `tool_calls` | local only |
| 003 | [litellm#31562](https://github.com/BerriAI/litellm/issues/31562) | args containing `"[DONE]"` falsely terminate the stream | any key + LiteLLM |
| 004 | [ollama#14567](https://github.com/ollama/ollama/issues/14567), [ccr#1431](https://github.com/musistudio/claude-code-router/issues/1431) | Gemini `thought_signature` dropped → 400 on next turn | Gemini key |
| 005 | [switchyard#178](https://github.com/NVIDIA-NeMo/Switchyard/issues/178) | tool_use id sanitizer is non-injective; corrupts Kimi-format ids | Switchyard + synthetic upstream |
| 006 | [ollama#7881](https://github.com/ollama/ollama/issues/7881) + stream-drop reports | OpenAI-compat: missing `tool_calls[].index`; calls lost under `stream:true` | local only |
| 007 | [vllm#45167](https://github.com/vllm-project/vllm/issues/45167) | `</tool_call>` inside a string argument drops the whole call | vLLM or synthetic replay |
| 008 | [sglang#29441](https://github.com/sgl-project/sglang/issues/29441) | empty-content chunk before tool chunks breaks Vercel AI SDK | synthetic replay |
| 009 | [litellm#26755](https://github.com/BerriAI/litellm/issues/26755) | multi-turn replay violates Gemini result-ordering rule | Gemini key + LiteLLM |
| 010 | [litellm#35303](https://github.com/BerriAI/litellm/issues/35303) | one malformed argument string → retry storm → OOM | LiteLLM, no key |

Backlog (from the research book, add as capacity allows): llama.cpp#26359
(server rejects its own id), litellm#32214 (sanitizer broke vLLM/Kimi),
litellm#27671 / #30053 (Responses bridge), ollama#17429 (role:tool hangs),
vllm#47903 (stream vs non-stream divergence on truncation).

## Claim a target

Unclaimed reproductions, roughly easiest first. Comment on the tracking issue
or just open a PR (see CONTRIBUTING.md).

- litellm#26755: Gemini multi-turn ordering violation via /v1/messages
- litellm#35303: one malformed argument string triggers a retry storm
- vllm#45167: literal </tool_call> inside a string argument drops the call
  (needs a vLLM instance or the offline rig)
- sglang#29441: empty content chunk before tool chunks breaks strict SDKs
- ollama#17429: role:"tool" message hangs the server (model-dependent)
- ~~Switchyard #242: reasoning/content chunk reorder~~ DONE (issues/010)
- ~~Switchyard #369: content_filter to end_turn~~ DONE (issues/010)
- ~~thinking history dropped/leaked on Anthropic request translate~~ DONE (issues/016)
- ~~disable_parallel_tool_use dropped~~ DONE (issues/017)
- ~~user document block dumped or deleted~~ DONE (issues/018)
- ~~Switchyard invents cache_control ephemeral~~ DONE (issues/019)
- litellm#32214: sanitize_tool_use_ids breaks multi-turn on vLLM/Kimi (same class as 005)
- litellm#27671: Responses streaming bridge, unregistered chatcmpl- text id
- litellm#30053: fast_path skips tool-call continuation hook
- bifrost#4065: Anthropic ingress returns end_turn instead of tool_use
- Any tool-call issue in a translation layer we have not covered: bring it
