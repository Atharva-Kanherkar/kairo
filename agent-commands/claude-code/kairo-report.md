---
description: Report a broken tool call to the kairo open dataset. Gathers the evidence from this session, redacts secrets, and files the report for you.
---

You are helping the user report a tool-call failure to kairo, an open dataset
of LLM gateway translation bugs (https://github.com/Atharva-Kanherkar/kairo).
The user just experienced a tool call that broke: a tool that never ran, an
agent that stalled after a tool call, empty arguments, or similar. Your job is
to turn that into a filed failure report with as little work for the user as
possible.

Follow these steps:

1. Collect the setup, inferring everything you can before asking. You likely
   already know the agent (you), and can often infer the model and gateway from
   the session. Ask the user only for what you cannot infer, in ONE short
   question, not an interview:
   - Agent/client (Claude Code, Cline, Cursor, Aider, ...)
   - Model (DeepSeek, Kimi, GLM, Qwen, local Ollama model, ...)
   - Gateway between them (LiteLLM, OpenRouter, claude-code-router, Switchyard,
     Ollama OpenAI-compat, direct, unknown)
   - What the API base URL points at, if configured (ANTHROPIC_BASE_URL,
     OPENAI_BASE_URL, or similar env vars; read them if accessible)

2. Describe the failure in two or three sentences from what you observed in
   this session: which tool call failed, what the user expected, what actually
   happened. Quote the failing tool call from the conversation if visible.

3. Gather wire evidence if it is cheap to get. Check, in order:
   - gateway logs the user can point you to
   - the agent's own session transcript on disk, if you know where it lives
   - otherwise skip; a good description alone is still a valid report
   Keep evidence snippets under ~100 lines.

4. REDACT before anything leaves the machine. Remove or mask:
   - API keys and tokens (anything matching sk-, key=, authorization headers)
   - private file paths, company names, proprietary code, personal data
   Show the user the exact final report text and ask for explicit confirmation
   before filing. Never file without the user saying yes.

5. File it. Build the issue body using the template below. Then:
   - If the gh CLI is available and authenticated:
     gh issue create --repo Atharva-Kanherkar/kairo --title "<title>" --body "<body>" --label data
   - Otherwise, print the report and this prefilled URL for the user to click:
     https://github.com/Atharva-Kanherkar/kairo/issues/new?template=tool-call-failure.md
   Title format: "[failure] <agent> + <model> via <gateway>: <one-line symptom>"

Issue body template:

## Setup
- Agent / client:
- Model:
- Gateway / proxy in between:
- API format the client speaks:

## What went wrong
<the two-or-three sentence description>

## Transcript
<redacted evidence, or "not captured; description only">

## Anything else
- Frequency (always / sometimes):
- Works when pointed directly at the model's own API? (if known):
- Reported via /kairo-report

Rules:
- One short confirmation, then act. Do not interrogate the user.
- Never include secrets, even redacted-looking ones, in the filed issue.
- If the user declines to file publicly, save the report to
  ./kairo-report-draft.md instead and tell them where it is.
