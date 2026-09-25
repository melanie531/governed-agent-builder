"""Local MCP server serving the platform-curated specialist over Streamable HTTP at /mcp.

Standard MCP SDK transport and lifecycle (initialize, notifications/initialized, tools/list,
tools/call), LOCAL SIMULATION only: tool bodies are the synthetic fixtures in backend.harness,
nothing calls AWS, Gateway or Snowflake. Governance is not forked: listing and calls go through
harness.list_tools / harness.call_tool, and served tools come from approved catalog mcpServer
targets (foundation_approval.tool_target).

Authorization context (caller manifest + grant scopes) comes ONLY from resolve_caller, a
server-side callable given the HTTP request. Tool-call arguments never carry identity or scope.
Production has no authenticated caller redemption wired, so the default resolver fails closed.

Run: uv run --with "mcp>=1.30,<2" python -m runtime.mcp_specialist.server
"""
import contextlib
import json
import os

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Route

from backend import harness
from backend.catalog import COMPONENTS
from backend.foundation_approval import tool_target


def served_tools():
    """Tool id -> approved mcpServer target record for every platform-curated specialist."""
    served = {}
    for component in COMPONENTS:
        if component.get("tool_type") != harness.GATEWAY or not component.get("approved") or component["id"] not in harness.SPECIALIST_AGENTS:
            continue
        target = tool_target(component)
        if target and target["type"] == "mcpServer":
            served[component["id"]] = target
    return served


def unauthenticated(request):
    raise harness.ToolDenied("AUTHENTICATED_CALLER_REQUIRED")


def caller_context(resolve_caller, request):
    """(definition, scopes) for this request from trusted server-side context; fail closed."""
    context = resolve_caller(request)
    if (not isinstance(context, dict) or set(context) != {"definition", "scopes"}
            or not isinstance(context["definition"], dict) or not isinstance(context["scopes"], dict)):
        raise harness.ToolDenied("AUTHENTICATED_CALLER_REQUIRED")
    return context["definition"], context["scopes"]


def denied(code):
    return types.CallToolResult(isError=True, content=[types.TextContent(type="text", text=code)])


def build_server(resolve_caller=unauthenticated):
    served = served_tools()
    server = Server("governed-specialist-mcp", instructions="LOCAL SIMULATION: synthetic fixtures only.")

    @server.list_tools()
    async def list_tools():
        definition, _ = caller_context(resolve_caller, server.request_context.request)
        return [types.Tool(name=t["name"], description=f"Platform-curated specialist via {harness.GATEWAY} (target {served[t['name']]['name']}); LOCAL SIMULATION.",
                           inputSchema=t["inputSchema"]) for t in harness.list_tools(definition) if t["name"] in served]

    # Arguments are checked by harness.call_tool, the single governance path, not by the SDK.
    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        try:
            definition, scopes = caller_context(resolve_caller, server.request_context.request)
            if name not in served:
                raise harness.ToolDenied("TOOL_NOT_SERVED")
            result = harness.call_tool(definition, name, arguments, scopes.get(name))
        except harness.ToolDenied as exc:
            return denied(str(exc))
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result))], structuredContent=result)

    return server


def create_app(resolve_caller=unauthenticated):
    if os.getenv("EXECUTION_MODE", "local") != "local":
        raise RuntimeError("AWS mode disabled: the MCP specialist server is LOCAL SIMULATION only. No simulation fallback.")
    # Stateless Streamable HTTP, the AgentCore Runtime MCP shape. Loopback hosts only.
    manager = StreamableHTTPSessionManager(app=build_server(resolve_caller), stateless=True, security_settings=TransportSecuritySettings(
        allowed_hosts=["127.0.0.1:*", "localhost:*"], allowed_origins=["http://127.0.0.1:*", "http://localhost:*"]))

    class MCPEndpoint:
        async def __call__(self, scope, receive, send):
            await manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            yield

    return Starlette(routes=[Route("/mcp", endpoint=MCPEndpoint(), methods=["GET", "POST", "DELETE"])], lifespan=lifespan)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.getenv("PORT", "8000")))
