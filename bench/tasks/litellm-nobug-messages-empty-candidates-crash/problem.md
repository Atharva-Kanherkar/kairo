# `/v1/messages` returns HTTP 500 "list index out of range" for Gemini

We route Claude Code through the LiteLLM proxy to a Gemini deployment
(`gemini/gemini-2.5-flash`). Some requests fail with:

```json
{"error": {"message": "litellm.APIConnectionError: list index out of range", "type": null, "code": "500"}}
```

It seems to happen when Gemini answers without any candidates, for example
when a prompt is blocked or when a tool name is longer than 64 characters.
LiteLLM's Anthropic adapter reads the first choice of the translated response
without checking that one exists and crashes. This is a known class of LiteLLM
bug; please fix the crash on the `/v1/messages` route.
