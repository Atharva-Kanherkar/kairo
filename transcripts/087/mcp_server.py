import os, json, threading
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError:
    from mcp.server.mcpserver import MCPServer

    MCP_V2 = True
else:
    MCP_V2 = False

LEDGER = Path(os.environ["LEDGER_PATH"])
PORT = int(os.environ["MCP_PORT"])
LOCK = threading.Lock()
if MCP_V2:
    mcp = MCPServer("kairo087")
else:
    mcp = FastMCP("kairo087", host="127.0.0.1", port=PORT, streamable_http_path="/mcp")

@mcp.tool()
def commit_write(entry: str) -> str:
    """Append one entry to the ledger and return a marker. Side-effecting, not idempotent."""
    with LOCK:
        n = sum(1 for _ in LEDGER.open()) if LEDGER.exists() else 0
        with LEDGER.open("a") as f:
            f.write(json.dumps({"seq": n + 1, "entry": entry}) + "\n")
    return f"KAIRO087_WROTE seq={n + 1} entry={entry}"

if __name__ == "__main__":
    if MCP_V2:
        mcp.run(
            transport="streamable-http",
            host="127.0.0.1",
            port=PORT,
            streamable_http_path="/mcp",
        )
    else:
        mcp.run(transport="streamable-http")
