#!/usr/bin/env python3
"""Deterministic stdio MCP server used by issue 074."""

import json
from pathlib import Path
import sys

from mcp.server.fastmcp import FastMCP


CALL_LOG = Path(sys.argv[1])
server = FastMCP("kairo-074")


@server.tool()
def echo(value: str) -> str:
    """Return the supplied value."""
    with CALL_LOG.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"tool": "echo", "value": value}) + "\n")
    return f"tool-result:{value}"


if __name__ == "__main__":
    server.run(transport="stdio")
