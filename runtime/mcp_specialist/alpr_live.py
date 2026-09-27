"""Live-only ALPR MCP entry. No deployment caller, fixture routing or default grants.

The custom header is a bearer *reference*, not identity. The IAM backend redeems it
once, binds the exact tool/arguments/run, and rechecks current policy on each read.
Gateway metadataConfiguration.allowedRequestHeaders and Runtime
requestHeaderConfiguration.requestHeaderAllowlist must both pass host smoke.
"""
import contextlib
import json
import math
import os
import re
import time

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Route

from backend import alpr, harness
from foundation_harness.config import digest
from foundation_harness.context import Denied
from foundation_harness.alpr_exchange import CALLER_HEADER
from foundation_harness.transport import capture_deadline
from runtime.mcp_specialist.server import denied, served_tools

TOOLS = ("agent-alpr-account-vehicle", "agent-alpr-billing-notice", "agent-alpr-remediation")
DEFINITION = {"tools": list(TOOLS), "component_versions": dict.fromkeys(TOOLS, "1")}


def live_config():
    if os.getenv("CALLER_SCOPE_PROFILE") is not None:
        raise harness.ToolDenied("ALPR_DEMO_AUTHORITY_FORBIDDEN")
    if (os.getenv("ALPR_VIEW_SOURCE") != "live"
            or os.getenv("ALPR_SNOWFLAKE_SSM_PREFIX") != "/governed-agent-builder/alpr"
            or os.getenv("AWS_REGION") != "us-west-2"
            or any(os.getenv(key) for key in ("ALPR_SNOWFLAKE_ACCOUNT", "ALPR_SNOWFLAKE_USER",
                                             "ALPR_SNOWFLAKE_KEY_PATH", "ALPR_SNOWFLAKE_KEY_SSM_PARAM"))):
        raise harness.ToolDenied("ALPR_LIVE_CONFIG_REQUIRED")


class LiveALPR:
    def __init__(self, exchange):
        self.exchange = exchange

    def call(self, reference, name, arguments):
        deadline = time.monotonic() + 20
        with capture_deadline(deadline, time.monotonic):
            return self._call(reference, name, arguments, deadline)

    def _call(self, reference, name, arguments, deadline):
        live_config()
        if not isinstance(reference, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", reference):
            raise harness.ToolDenied("ALPR_CALLER_REFERENCE_REQUIRED")
        if name not in TOOLS or name not in served_tools():
            raise harness.ToolDenied("TOOL_NOT_SERVED")
        if (not isinstance(arguments, dict) or set(arguments) != {"question"}
                or not isinstance(arguments["question"], str) or not 1 <= len(arguments["question"]) <= 4000):
            raise harness.ToolDenied("AGENT_TOOL_ARGUMENTS_REJECTED")
        expected = digest(arguments)
        bound = self.exchange.request(reference, "redeem", tool=name, arguments_digest=expected)

        def check(value):
            if (not isinstance(value, dict) or value.get("reference") != reference
                    or value.get("tool") != name or value.get("version") != "1"
                    or value.get("arguments_digest") != expected
                    or type(value.get("expires_at")) not in (int, float)
                    or not math.isfinite(value["expires_at"]) or value["expires_at"] <= time.time()
                    or not isinstance(value.get("binding_digest"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", value["binding_digest"])
                    or not isinstance(value.get("scope"), dict)):
                raise harness.ToolDenied("ALPR_CALL_BINDING_DENIED")

        check(bound)

        def recheck(operation, **extra):
            live_config()
            value = self.exchange.request(reference, operation, binding_digest=bound["binding_digest"], **extra)
            check(value)
            if value["binding_digest"] != bound["binding_digest"] or value["scope"] != bound["scope"]:
                raise harness.ToolDenied("ALPR_CALL_BINDING_DENIED")

        recheck("authorize")
        with alpr.live_access(lambda view: recheck("view", view=view), deadline):
            result = harness.call_tool(DEFINITION, name, arguments, bound["scope"])
            # A successful live response always has query evidence, even for no rows.
            if any(not inner.get("snowflake_query_ids") or any(
                    not entry.get("snowflake_query_id") for entry in inner["snowflake_query_ids"])
                   for inner in result["inner_calls"]):
                raise harness.ToolDenied("ALPR_LIVE_QUERY_EVIDENCE_REQUIRED")
            recheck("finish")
            alpr.live_timeout(20)
        return {**result, "synthetic": True, "policy_notice": "Billing policies are DEMO ASSUMPTIONS."}

    def list(self, request):
        # The exchange verifies B's exclusive IAM/Gateway ingress for platform
        # listing. It never returns business invocation authority.
        return self.exchange.list_tools(request)


def configured_runtime():
    live_config()
    try:
        config = json.loads(os.environ["ALPR_LIVE_CONFIG"])
    except (KeyError, ValueError):
        raise harness.ToolDenied("ALPR_LIVE_CONFIG_REQUIRED") from None
    from foundation_harness.alpr_exchange import SpecialistExchange
    import boto3
    return LiveALPR(SpecialistExchange(boto3.Session(region_name="us-west-2"), config))


def create_app(runtime=None):
    live_config()
    runtime = runtime or configured_runtime()
    server = Server("governed-alpr-live-mcp", version="1",
                    instructions="Read-only live approved Snowflake views. SYNTHETIC data; policy DEMO ASSUMPTIONS.")

    @server.list_tools()
    async def list_tools():
        selected = await anyio.to_thread.run_sync(runtime.list, server.request_context.request)
        if not isinstance(selected, list) or not set(selected) <= set(TOOLS):
            raise harness.ToolDenied("ALPR_LISTING_AUTHORITY_REQUIRED")
        return [types.Tool(name=t["name"], description="Read-only ALPR specialist over live approved views; SYNTHETIC data.",
                           inputSchema=t["inputSchema"]) for t in harness.list_tools(DEFINITION)
                if t["name"] in selected and t["name"] in served_tools()]

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        try:
            request = server.request_context.request
            values = request.headers.getlist(CALLER_HEADER) if request else []
            reference = values[0] if len(values) == 1 else None
            result = await anyio.to_thread.run_sync(runtime.call, reference, name, arguments)
            return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result))],
                                        structuredContent=result)
        except (harness.ToolDenied, alpr.ViewUnavailable, Denied) as exc:
            return denied(str(exc))
        except Exception:
            return denied("ALPR_LIVE_CALL_FAILED")

    manager = StreamableHTTPSessionManager(app=server, stateless=True, json_response=True,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False))

    class Endpoint:
        async def __call__(self, scope, receive, send):
            await manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            yield

    return Starlette(routes=[Route("/mcp", endpoint=Endpoint(), methods=["GET", "POST", "DELETE"])], lifespan=lifespan)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host="0.0.0.0", port=8000)
