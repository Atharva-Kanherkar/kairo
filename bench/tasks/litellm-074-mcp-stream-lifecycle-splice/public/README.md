# Reproduction kit

`reproduce.sh` starts the real `litellm` proxy CLI from `/work/repo` (with a
master key), a real stdio MCP server (`mcp_server.py`, one `echo` tool that
logs each call), and a deterministic Responses-API upstream (`upstream.py`):
the first model round returns a function call, the second returns a final text
answer. It sends the reported streaming request and prints the raw SSE body the
client received, the MCP call log, and what the OpenAI SDK makes of the stream.
Proxy log: `/tmp/litellm-proxy.log`.
