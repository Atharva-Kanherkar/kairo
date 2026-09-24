# Browser-agent screenshots reach the model as a JSON string instead of an image

Our computer-use agent returns screenshots inside Anthropic `tool_result`
blocks and talks to Switchyard's `/v1/messages`, backed by an OpenAI-compatible
vision model. The model never "sees" the screenshots. In the request
Switchyard sends upstream, the tool message's text contains the whole
Anthropic image block serialized as JSON, base64 and all:

```json
{"role": "tool", "tool_call_id": "toolu_1",
 "content": "screen captured {\"type\":\"image\",\"source\":{\"type\":\"base64\",\"media_type\":\"image/png\",\"data\":\"iVBOR...\"}}"}
```

So the backend receives a long string rather than an image, pays for it as
text tokens, and cannot look at it. Images that users send directly in a user
message are translated to proper `image_url` parts. Tool-result images should
reach a vision backend as images too, in a request the OpenAI Chat API accepts.
