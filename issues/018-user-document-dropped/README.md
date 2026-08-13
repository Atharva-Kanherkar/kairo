# 018, user document blocks are stringified or deleted

- **Upstream**: same class as issue 007 (tool_result multimodal), but this is
  a **user** document on the inbound Anthropic request, not a tool result.
- **Tools under test**: Switchyard 0.2.0; LiteLLM 1.96.2.
- **Method**: offline capture rig. No keys.
- **Reproduced**: 2026-08-13. Evidence: `transcripts/016/cap-document.jsonl`
  (Switchyard), `transcripts/016/cap-litellm-document.jsonl` (LiteLLM).

## What breaks

An Anthropic user message with a text block plus a `document` block:

```
[{type: text, text: "summarize this"},
 {type: document, source: {type: text, media_type: text/plain, data: "THE DOCUMENT BODY"}}]
```

### Switchyard (Anthropic -> OpenAI Chat)

The document is JSON-dumped into a second text part:

```
{"text": "{\"source\":{\"data\":\"THE DOCUMENT BODY\",...},\"type\":\"document\"}", "type": "text"}
```

The model sees a JSON string, not a document. Same encode path as 007
(`ContentBlock::Unknown` -> `json_string`).

### LiteLLM (Anthropic -> Responses)

The document is **deleted**. Forwarded input is only:

```
{"type": "input_text", "text": "summarize this"}
```

`THE DOCUMENT BODY` is absent. Silent HTTP 200. Worse than Switchyard: there
is nothing left to even grep for.

## Why it matters

Claude Code file attachments, PDF tools, and any client that sends Anthropic
`document` blocks through a gateway will summarize / answer from a prompt
that no longer contains the file. The agent looks broken; the model never
saw the bytes.

## Test invariants

1. A non-text user block's payload MUST appear in the forwarded request.
2. A non-text block MUST translate to the target's native part (or fail
   loud). JSON-dumping it into `type: text` is a violation.
3. Silent deletion is a violation.

## Repro

```
python tools/capture_server.py $PWD/transcripts/016/cap-document.jsonl &
tools/switchyard/target/release/switchyard-server --config tools/switchyard-capture.toml --port 9000 &
curl -s localhost:9000/v1/messages -H 'anthropic-version: 2023-06-01' \
  -d @transcripts/016/req-document.json
# Switchyard: document JSON-dumped into a text part
# LiteLLM against a Responses mock: document gone, only "summarize this" remains
```
