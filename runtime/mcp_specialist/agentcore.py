"""AgentCore Runtime container entrypoint for the governed specialist MCP server (Path A).

AgentCore Runtime (MCP protocol) forwards Streamable HTTP to the container on 0.0.0.0:8000/mcp.
Authentication happens BEFORE the container: the Runtime's default IAM authorizer admits only
SigV4 requests from principals allowed bedrock-agentcore:InvokeAgentRuntime on this runtime,
which in this deployment is the Gateway service role alone. Host-header DNS-rebinding checks
are therefore off here (the SDK's own default for a non-loopback bind).

The caller context is bound to the deployment, server-side: one demo caller whose manifest pins
the specialist and whose grant scope is fixed at startup by CALLER_SCOPE_PROFILE (runtime config,
not request data): "full" is the specialist's full tool_scope, "no-data" grants the operations but
no data views, so the governance boundary denies. Nothing in the HTTP request (arguments,
headers, body) is read to decide authorization. Tool bodies remain LOCAL
SIMULATION (synthetic fixtures, EXECUTION_MODE=local); nothing calls Snowflake.
"""
import os

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings

from backend.harness import tool_scope
from runtime.mcp_specialist import server

AGENT_TOOL = "agent-risk-analyst"
SCOPE_PROFILES = {"full": tool_scope(AGENT_TOOL), "no-data": {"operations": tool_scope(AGENT_TOOL)["operations"], "data": []}}
DEPLOYMENT_CALLER = {"definition": {"tools": [AGENT_TOOL], "component_versions": {AGENT_TOOL: "1"}},
                     "scopes": {AGENT_TOOL: SCOPE_PROFILES[os.getenv("CALLER_SCOPE_PROFILE", "full")]}}


def deployment_caller(request):
    return {"definition": dict(DEPLOYMENT_CALLER["definition"]), "scopes": dict(DEPLOYMENT_CALLER["scopes"])}


if __name__ == "__main__":
    print(f"governed-specialist-mcp cold start image_build={os.getenv('IMAGE_BUILD', 'unknown')} caller_scope_profile={os.getenv('CALLER_SCOPE_PROFILE', 'full')}", flush=True)
    uvicorn.run(server.create_app(deployment_caller, TransportSecuritySettings(enable_dns_rebinding_protection=False)),
                host="0.0.0.0", port=8000)
