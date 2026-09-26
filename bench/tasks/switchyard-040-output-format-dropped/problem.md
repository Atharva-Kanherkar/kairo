# Structured output is ignored on `/v1/messages`: the JSON schema never reaches the backend

We ask for structured output with Anthropic's `output_format` through
Switchyard's `/v1/messages` (OpenAI-compatible backend behind it):

```json
{"model": "captured-model", "max_tokens": 256,
 "messages": [{"role": "user", "content": "Where is the Eiffel Tower?"}],
 "output_format": {"type": "json_schema", "schema": {"type": "object",
   "properties": {"city": {"type": "string"}}, "required": ["city"], "additionalProperties": false}}}
```

The model answers in free text (sometimes a markdown code fence) and our JSON
parsing fails. The request Switchyard forwards has no `response_format` at
all. Sending the equivalent OpenAI `response_format` to Switchyard's
`/v1/chat/completions` works, so the backend supports it; the Anthropic
setting is simply lost.
