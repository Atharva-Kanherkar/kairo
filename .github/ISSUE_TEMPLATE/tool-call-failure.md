---
name: Report a tool-call failure (donate a transcript)
about: You run a coding agent on a non-default model and tool calls break. Share what happened. No Rust needed.
title: "[failure] "
labels: data
---

You do not need to know why it broke. A real example of a broken run is exactly
the open data this project needs. Fill in what you can.

## Setup

- Agent / client: (Claude Code, Cline, Cursor, Aider, OpenClaw, ...)
- Model: (DeepSeek, Kimi K2, GLM, Qwen, a local Ollama model, ...)
- Gateway / proxy in between: (LiteLLM, OpenRouter, claude-code-router, Switchyard, Ollama compat, direct, ...)
- API format the client speaks: (Anthropic Messages, OpenAI Chat, Responses, not sure)

## What went wrong

What did the agent do or fail to do? Examples: a tool never ran, a file was not
written, the agent stalled after a tool call, arguments came through empty, an
error you did not expect.

## Transcript (the valuable part, if you can get it)

Paste the request and the response bytes if you have them. Redact anything
private first. Never paste API keys.

- Request sent by the client:
- Response received (the raw SSE or JSON if possible):

Tips for capturing: many gateways log requests, or you can set a proxy log. If
you cannot get the bytes, describe the failure anyway; it still helps us find
the pattern.

## Anything else

Does it happen every time or sometimes? Does it work when you point the same
agent at the model's own API directly? That last one is gold.
