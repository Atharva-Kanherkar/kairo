# Reproduction kit

`reproduce.sh` rebuilds the Bifrost HTTP gateway from `/work/repo` and starts
it in MCP agent mode against a local streamable-HTTP MCP server
(`mcp_server.py`: `charge` and `credit` auto-execute and log each execution,
`review` is manual) and a deterministic OpenAI-compatible upstream
(`upstream.py`) whose first turn asks for `charge(alpha)`, `charge(beta)`, and
`review`. It prints the chat completion the client received and the MCP
executions. Gateway log: `/tmp/bifrost.log`.
