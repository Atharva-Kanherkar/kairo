# Requests with long tool names crash Bifrost's Anthropic endpoint

Our MCP bridge generates tool names by joining server and tool names, and some
end up longer than 64 characters. LiteLLM is known to fail on these with an
unhandled `IndexError` and an HTTP 500 ("list index out of range"). We saw
errors when we switched to Bifrost as well and assume it has the same crash.

```bash
curl -s localhost:8080/anthropic/v1/messages -H 'content-type: application/json' -d '{
  "model": "openai/gpt-4o", "max_tokens": 64, "tool_choice": {"type": "any"},
  "messages": [{"role": "user", "content": "go"}],
  "tools": [{"name": "<300 characters>", "description": "t", "input_schema": {"type": "object", "properties": {}}}]}'
```

Please make Bifrost handle long tool names without crashing.
