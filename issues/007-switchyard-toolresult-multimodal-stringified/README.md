# 007 — Switchyard stringifies image/document blocks in tool_result (model goes blind)

- **Tool under test**: Switchyard `switchyard-server` 0.2.0 (2026-08-12 build).
- **Method**: offline capture rig (`tools/mock_upstream.py`) records the exact
  bytes Switchyard sends to an OpenAI backend after translating an Anthropic
  request. No keys.
- **Reproduced**: 2026-08-12. Evidence: `transcripts/007/capA.jsonl` (image),
  `capB.jsonl` (document), `out-anthropic.sse` (streaming control).

## Bug — non-text tool_result blocks are JSON-dumped into a text string

An Anthropic `tool_result` whose content is an array of blocks
`[{type:text}, {type:image}]` should translate to an OpenAI `tool` message that
carries the image as a real content part (`image_url`) — or, if the target
can't take images in a tool role, be surfaced as an explicit failure. Switchyard
does neither: it concatenates the blocks into one **string**, dumping the
non-text block as raw JSON.

**Image (`capA.jsonl`), what the OpenAI backend received:**
```
role: "tool"
content: 'here it is: {"source":{"data":"iVBORw0KGgoAAA...==","media_type":"image/png","type":"base64"},"type":"image"}'
```
`image_url` content part present: **False**. The model receives the literal
base64 string glued to the text — it cannot see the image at all.

**Document (`capB.jsonl`), same class:**
```
content: 'result text {"source":{"data":"DOCBODY","media_type":"text/plain","type":"text"},"type":"document"}'
```
The document survives only as JSON text, not as a usable part.

Impact: any agent that returns a screenshot, PDF, or image from a tool (browser
agents, document tools, vision loops) is silently broken through Switchyard —
the tool "returns" data the model can never actually read. Fails **silent**,
HTTP 200.

## What is NOT broken (kept honest)

- **Streaming tool-call translation is correct.** A canned OpenAI stream with
  the arguments split across deltas (`{"city":` + ` "Paris"}`) translated to a
  legal Anthropic SSE sequence: `content_block_start` (tool_use) →
  two `input_json_delta` → `content_block_stop` → `stop_reason: tool_use`.
  Reassembled args are valid JSON, event grammar legal. (`out-anthropic.sse`).
  Switchyard's incremental re-encoder works — this is a real strength.
- Multiple plain-text blocks in one message are joined with `\n` (acceptable).

## Test invariants

1. A non-text block (image/document/audio) in a tool_result MUST translate to
   the target's native multimodal representation, OR be surfaced as an explicit
   unsupported-feature error — never silently serialized into a text string.
2. A tool result that carried an image on the way in must carry a
   model-readable image on the way out (round-trip fidelity for multimodal
   tool results).

## Repro

```
tools/litellm-env/bin/python tools/mock_upstream.py 9999 $PWD/transcripts/007/capA.jsonl $PWD/transcripts/007/canned-openai.json &
tools/switchyard/target/release/switchyard-server --config tools/switchyard-capture.toml --port 9000 &
# send an Anthropic tool_result containing a text + image block (see transcript), then:
python3 -c "import json;r=[json.loads(l) for l in open('transcripts/007/capA.jsonl')][-1]['body'];print([m for m in r['messages'] if m['role']=='tool'][0]['content'])"
```
