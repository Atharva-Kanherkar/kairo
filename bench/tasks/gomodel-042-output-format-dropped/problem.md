# Structured output requested through `/v1/messages` is ignored

We call GoModel's Anthropic-compatible `/v1/messages` with structured output,
backed by an OpenAI model:

```json
{"model": "captured-model", "max_tokens": 256,
 "messages": [{"role": "user", "content": "Where is the Eiffel Tower?"}],
 "output_config": {"format": {"type": "json_schema", "schema": {
   "type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"],
   "additionalProperties": false}}}}
```

The model replies in free-form prose or a fenced code block and our JSON
parsing fails. The chat completion GoModel sends to the provider carries no
`response_format`. Older clients that send the same format object at the top
level as `output_format` see the same thing. Sending the OpenAI-style
`response_format` to GoModel's `/v1/chat/completions` works, so the provider
supports it; the Anthropic setting is lost in translation.
