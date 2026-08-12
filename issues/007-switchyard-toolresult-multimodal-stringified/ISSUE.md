title: [bug] image and document blocks in tool_result are serialized into a text string when translating to OpenAI Chat

## Symptom

An Anthropic `tool_result` whose content is a list of blocks (for example a text block plus an image block) is translated to an OpenAI Chat `tool` message as a single string, with the non-text block dumped as raw JSON. The image is never emitted as an `image_url` content part, so a downstream vision model cannot see it. No error is raised and the response is 200.

## Reproduction

Inbound format: Anthropic Messages. Route: passthrough to an `openai_chat` target. A small mock backend is used only to record the exact bytes Switchyard forwards.

routes.toml:

```toml
schema_version = 1
[llm_clients.local]
format = "openai_chat"
base_url = "http://127.0.0.1:9999/v1"
[llm_clients.local.extra_headers]
authorization = "Bearer x"
[targets.cap]
id = "captured-model"
llm_client = "local"
[routes.primary]
id = "captured-model"
type = "passthrough"
target = "cap"
```

```bash
switchyard-server --config routes.toml --port 9000

curl -s http://localhost:9000/v1/messages \
  -H 'content-type: application/json' \
  -H 'anthropic-version: 2023-06-01' \
  -d '{
    "model": "captured-model",
    "max_tokens": 100,
    "tools": [{"name":"screenshot","description":"take a screenshot","input_schema":{"type":"object","properties":{"t":{"type":"string"}},"required":["t"]}}],
    "messages": [
      {"role":"user","content":"screenshot the page"},
      {"role":"assistant","content":[{"type":"tool_use","id":"toolu_1","name":"screenshot","input":{"t":"page"}}]},
      {"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_1","content":[
        {"type":"text","text":"here it is:"},
        {"type":"image","source":{"type":"base64","media_type":"image/png","data":"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="}}
      ]}]}
    ]
  }'
```

## Expected vs. actual

- **Expected:** the tool result image is forwarded as an OpenAI `image_url` content part (the standard way OpenAI Chat carries images), or the request is rejected with a clear unsupported-modality error if the target cannot accept images in a tool role.
- **Actual:** the forwarded `tool` message content is a plain string with the image block serialized as JSON. No `image_url` part is present.

```
role: "tool"
content: "here it is: {\"source\":{\"data\":\"iVBORw0KGgoAAA...==\",\"media_type\":\"image/png\",\"type\":\"base64\"},\"type\":\"image\"}"
```

A `document` block in a tool_result behaves the same way (serialized into the text string rather than mapped to a content part).

## Environment

- Switchyard version (or commit SHA): 2bef154 (2bef154970d23cacf9c83b4fe9c1cd90212623e8), release build from source
- Python version: 3.9.6
- OS / arch: macOS arm64
- Install path: source build (`cargo build --release -p switchyard-server`)
- Inbound format: Anthropic Messages
- Backend: OpenAI-compatible (any; a local mock was used to capture the forwarded request)

## Additional context

Impact is highest for agents that return images or documents from tools (browser automation, screenshot tools, document readers): the tool appears to return data but the model receives base64 as literal text and is blind to it. Streaming tool-call translation looked correct in the same setup; this report is only about non-text blocks inside `tool_result`. Possibly related to #152 (filtering unsupported input modalities per target), though here the block is neither filtered nor rejected, it is silently stringified.
