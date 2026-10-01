"""Deterministic stdio MCP server: one `echo` tool that appends each call to a log file."""

import json
import sys
from pathlib import Path

try:  # mcp 2.x renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as Server

CALL_LOG = Path(sys.argv[1])
server = Server("kairo-mcp-echo")


@server.tool()
def echo(value: str) -> str:
    """Return the supplied value."""
    with CALL_LOG.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"tool": "echo", "value": value}) + "\n")
    return f"tool-result:{value}"


if __name__ == "__main__":
    server.run(transport="stdio")
