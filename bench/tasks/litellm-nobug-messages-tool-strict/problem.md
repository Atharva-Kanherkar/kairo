# `strict: true` on Anthropic tools is dropped when `/v1/messages` goes to an OpenAI model

We rely on strict tool schemas. Our Anthropic-format requests to the LiteLLM
proxy mark each tool with `"strict": true`:

```json
{"model": "mock", "max_tokens": 256,
 "messages": [{"role": "user", "content": "book a table for two"}],
 "tools": [{"name": "book_table", "description": "Book a restaurant table", "strict": true,
            "input_schema": {"type": "object", "properties": {"party": {"type": "integer"}},
                             "required": ["party"], "additionalProperties": false}}]}
```

The deployment is an OpenAI model. We are seeing tool arguments that do not
match the schema (wrong types, extra keys), which strict mode should rule out,
so it looks like LiteLLM strips `strict` when it translates the tools for
OpenAI. This was reported against LiteLLM before; please make sure the flag is
passed through.
