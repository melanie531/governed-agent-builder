"""A complete generic MCP: entry point, support module, data file and dependencies."""
import os

from mcp.server.fastmcp import FastMCP
from package_tools import package_greeting

mcp = FastMCP("Complete package example", host="0.0.0.0",
              port=int(os.environ.get("MCP_PORT", "8000")), stateless_http=True)
mcp.tool()(package_greeting)

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
