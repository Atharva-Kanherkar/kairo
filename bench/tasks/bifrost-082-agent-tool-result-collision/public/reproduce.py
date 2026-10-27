from kairo_verify import Upstream, show
from rigs import bifrost

from mcp_server import MCPServer
from upstream import chat_body, gateway_config, respond

with Upstream(respond) as up, MCPServer() as mcp, bifrost.Gateway(gateway_config(up.url, mcp.url)) as gw:
    status, body, raw = gw.post_json("/v1/chat/completions", chat_body("repeated"))
    show(f"client received HTTP {status}", raw)
    show("MCP executions", mcp.executions)
