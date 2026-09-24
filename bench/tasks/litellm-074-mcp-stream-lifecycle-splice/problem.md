# Streaming `/v1/responses` with an auto-executed MCP tool breaks the OpenAI SDK

We expose an MCP server through the LiteLLM proxy and let the proxy execute it
automatically (`require_approval: "never"`). Non-streaming works. With
`stream: true` the official OpenAI Python SDK dies part-way through the stream
and never returns the final answer:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:4000/v1", api_key="sk-...")
with client.responses.stream(
    model="mock",
    input="Use the echo tool with PING, then answer.",
    tools=[{"type": "mcp", "server_url": "litellm_proxy/mcp/demo",
            "server_label": "demo", "require_approval": "never"}],
    tool_choice="required",
) as stream:
    for event in stream:
        pass
    print(stream.get_final_response())
```

The SDK raises an `AssertionError` from its stream accumulator. The MCP tool
did run, and the model's follow-up answer is somewhere in the raw SSE body,
but the stream the client receives does not read as one coherent response.

With `require_approval: "always"`, or with no tools at all, the same client
code works. Config (abridged):

```yaml
model_list:
  - model_name: mock
    litellm_params: {model: openai/mockmodel, api_base: "http://127.0.0.1:9000/v1", api_key: "..."}
mcp_servers:
  demo:
    transport: stdio
    command: python
    args: ["mcp_server.py"]
general_settings:
  master_key: sk-...
```
