import os, json, threading
from pathlib import Path
from mcp.server.fastmcp import FastMCP

LEDGER = Path(os.environ["LEDGER_PATH"])
PORT = int(os.environ["MCP_PORT"])
LOCK = threading.Lock()
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
    mcp.run(transport="streamable-http")
