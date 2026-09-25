"""Contract tests for the specialist-as-MCP (Runtime A -> Gateway mcpServer target -> Runtime B)
delivery path. Offline synthetic doubles only; never evidence of live cloud behavior.

Scope of each pin (no over-assertion):
1. foundation_approval.platform_metadata admits a correctly configured ``mcp.mcpServer`` tools
   target (Runtime MCP endpoint + GATEWAY_IAM_ROLE with an explicit IamCredentialProvider
   service, per the bedrock-agentcore-control service model where IamCredentialProvider
   REQUIRES ``service``) and fails closed with a controlled 409 -- never a KeyError crash --
   for unknown target shapes, wrong credentials, wrong endpoints, or schema mismatches.
2. GatewayMCP -- this specific client -- rejects non-Gateway endpoints at construction and
   signs every RPC it sends with SigV4 for the bedrock-agentcore service. This proves only
   the behavior of this client class, not that the runtime as a whole cannot reach other
   endpoints by other means.
3. infra/journey.template GatewayRole has no bedrock-agentcore:InvokeAgentRuntime permission
   today (flips when the outbound-signing slice is implemented).
"""
import json
import re
from types import SimpleNamespace

import httpx
import pytest
from botocore.credentials import Credentials
from fastapi import HTTPException

from foundation_harness import journey_mcp
from foundation_harness.config import digest
from foundation_harness.journey_mcp import GatewayMCP
from scripts.foundation_probe import example_config

ACCOUNT = "998877665544"  # synthetic 12-digit account id


# ---------------------------------------------------------------------------
# 1. Approval path: mcpServer tools target verification (fail closed, no KeyError)
# ---------------------------------------------------------------------------

RUNTIME_B_ENDPOINT = ("https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/"
                      "arn%3Aaws%3Abedrock-agentcore%3Aus-west-2%3A" + ACCOUNT
                      + "%3Aruntime%2Fgab_specialist_synthetic-abc123/invocations?qualifier=DEFAULT")

IAM_SIGV4_CREDENTIALS = [{"credentialProviderType": "GATEWAY_IAM_ROLE",
                          "credentialProvider": {"iamCredentialProvider": {"service": "bedrock-agentcore"}}}]


class FakeCF:
    def describe_stacks(self, StackName):
        outputs = {"governed-agent-builder-serverless-app": {
                       "ApiEndpoint": "https://synthetic.execute-api.us-west-2.amazonaws.com/"},
                   "governed-agent-builder-foundation-m0": {
                       "FoundationRole": f"arn:aws:iam::{ACCOUNT}:role/synthetic-foundation",
                       "ModelGateway": "mg-synthetic", "ToolsGateway": "tg-synthetic"}}[StackName]
        return {"Stacks": [{"Outputs": [{"OutputKey": k, "OutputValue": v} for k, v in outputs.items()]}]}


class FakeControl:
    def __init__(self, tools_target_configuration, tools_credentials=None):
        self.urls = {"mg-synthetic": "https://gab-foundation-model-m0-offline.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp",
                     "tg-synthetic": "https://gab-foundation-tools-m0-offline.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp"}
        self.tools_target_configuration = tools_target_configuration
        self.tools_credentials = tools_credentials
        self.model_target_configuration = {"inference": {"provider": {"endpoint": "synthetic"}}}

    def get_gateway(self, gatewayIdentifier):
        return {"gatewayUrl": self.urls[gatewayIdentifier], "status": "READY", "authorizerType": "AWS_IAM"}

    def list_gateway_targets(self, gatewayIdentifier, maxResults):
        return {"items": [{"targetId": gatewayIdentifier + "-t1"}]}

    def get_gateway_target(self, gatewayIdentifier, targetId):
        configuration = (self.model_target_configuration if gatewayIdentifier == "mg-synthetic"
                         else self.tools_target_configuration)
        detail = {"status": "READY", "name": "fixture", "targetConfiguration": configuration}
        if gatewayIdentifier != "mg-synthetic" and self.tools_credentials is not None:
            detail["credentialProviderConfigurations"] = self.tools_credentials
        return detail


class FakeStudioTarget:
    def __init__(self, control):
        self.account, self._control = ACCOUNT, control

    def verify(self):
        pass

    def client(self, name):
        return {"cloudformation": FakeCF(), "bedrock-agentcore-control": self._control}[name]


def approval_raw(control):
    raw = example_config()
    raw["model"]["targetDigest"] = digest(control.model_target_configuration)
    return raw


# Approved catalog record for the Runtime-B target (backend.foundation_approval.tool_target shape).
APPROVED_RUNTIME_B = {"fixture": {"type": "mcpServer", "name": "fixture", "endpoint": RUNTIME_B_ENDPOINT,
                                  "outbound_auth": "GATEWAY_IAM_ROLE"}}


def call_platform_metadata(monkeypatch, control, discovered=None, approved=APPROVED_RUNTIME_B):
    import scripts.foundation_target as foundation_target
    from backend import foundation_approval
    monkeypatch.setattr(foundation_target, "StudioTarget", lambda session: FakeStudioTarget(control))
    if discovered is not None:
        monkeypatch.setattr(foundation_approval, "gateway_tools", lambda session, url: discovered)
    return foundation_approval.platform_metadata(approval_raw(control), approved)


def matching_discovery():
    raw = example_config()
    return [{"name": raw["tools"][0]["name"], "description": raw["tools"][0]["description"],
             "inputSchema": raw["tools"][0]["inputSchema"]}]


def mcp_server_control(endpoint=RUNTIME_B_ENDPOINT, credentials=IAM_SIGV4_CREDENTIALS):
    return FakeControl({"mcp": {"mcpServer": {"endpoint": endpoint}}}, tools_credentials=credentials)


def test_lambda_tools_target_passes_metadata_verification(monkeypatch):
    """Baseline: the shipped mcp.lambda contract admits a matching tools target."""
    raw = example_config()
    control = FakeControl({"mcp": {"lambda": {"lambdaArn": "arn:aws:lambda:us-west-2:" + ACCOUNT + ":function:synthetic",
        "toolSchema": {"inlinePayload": [{"name": "lookup", "inputSchema": raw["tools"][0]["inputSchema"]}]}}}})
    result = call_platform_metadata(monkeypatch, control)
    assert result["role"].endswith(":role/synthetic-foundation")


def test_mcpserver_target_with_iam_sigv4_provider_and_matching_discovery_passes(monkeypatch):
    """A specialist Runtime-B mcpServer target is admissible when the endpoint is a same-region
    AgentCore Runtime MCP URL, the credential configuration is GATEWAY_IAM_ROLE with an explicit
    IamCredentialProvider service (required by the control-plane model), and Gateway discovery
    returns exactly the approved tool names and input schemas."""
    control = mcp_server_control()
    result = call_platform_metadata(monkeypatch, control, discovered=matching_discovery())
    assert result["role"].endswith(":role/synthetic-foundation")


def test_unknown_tools_target_shape_fails_closed_with_409(monkeypatch):
    """An mcp target that is neither lambda nor mcpServer must yield a controlled 409,
    never an uncontrolled KeyError."""
    control = FakeControl({"mcp": {"openApiSchema": {"s3": {"uri": "s3://synthetic/schema.json"}}}})
    with pytest.raises(HTTPException) as excinfo:
        call_platform_metadata(monkeypatch, control, discovered=matching_discovery())
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == "TOOL_TARGET_SCHEMA_REQUIRED"


def test_mcpserver_target_without_iam_service_name_is_rejected(monkeypatch):
    """A bare GATEWAY_IAM_ROLE entry (no IamCredentialProvider service) is NOT a valid SigV4
    configuration for an mcpServer target: the bedrock-agentcore-control service model marks
    IamCredentialProvider.service as required. Fail closed with a controlled 409."""
    control = mcp_server_control(credentials=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}])
    with pytest.raises(HTTPException) as excinfo:
        call_platform_metadata(monkeypatch, control, discovered=matching_discovery())
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == "IAM_SIGV4_CREDENTIAL_PROVIDER_REQUIRED"


def test_mcpserver_target_with_foreign_endpoint_is_rejected(monkeypatch):
    """An mcpServer endpoint outside the same-region AgentCore Runtime data plane
    (e.g. an arbitrary external host) must be rejected with a controlled 409."""
    control = mcp_server_control(endpoint="https://synthetic-runtime-b.example.invalid/mcp")
    with pytest.raises(HTTPException) as excinfo:
        call_platform_metadata(monkeypatch, control, discovered=matching_discovery())
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == "RUNTIME_MCP_SERVER_ENDPOINT_REQUIRED"


def test_mcpserver_target_with_mismatched_discovered_schema_is_rejected(monkeypatch):
    """Discovered tool schemas that differ from the approved manifest must fail closed."""
    drifted = matching_discovery()
    drifted[0]["inputSchema"] = {"type": "object", "properties": {"injected": {"type": "string"}}}
    control = mcp_server_control()
    with pytest.raises(HTTPException) as excinfo:
        call_platform_metadata(monkeypatch, control, discovered=drifted)
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == "TOOL_TARGET_SCHEMA_REQUIRED"


def test_mcpserver_target_with_missing_discovered_tool_is_rejected(monkeypatch):
    """A target whose discovery does not expose the approved tool name must fail closed."""
    control = mcp_server_control()
    with pytest.raises(HTTPException) as excinfo:
        call_platform_metadata(monkeypatch, control, discovered=[])
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == "TOOL_TARGET_SCHEMA_REQUIRED"


def test_mcpserver_target_discovery_failure_is_a_controlled_409(monkeypatch):
    """If Gateway discovery itself fails, approval must fail closed with a controlled 409,
    not propagate an uncontrolled transport exception."""
    import scripts.foundation_target as foundation_target
    from backend import foundation_approval
    control = mcp_server_control()
    monkeypatch.setattr(foundation_target, "StudioTarget", lambda session: FakeStudioTarget(control))

    def broken(session, url):
        raise journey_mcp.GatewayFailure("Gateway returned HTTP 503")
    monkeypatch.setattr(foundation_approval, "gateway_tools", broken)
    with pytest.raises(HTTPException) as excinfo:
        foundation_approval.platform_metadata(approval_raw(control), APPROVED_RUNTIME_B)
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == "TOOL_TARGET_DISCOVERY_REQUIRED"


def test_runtime_mcpserver_target_without_approved_catalog_record_is_rejected(monkeypatch):
    """A correctly configured Runtime-B target is still not admissible unless it matches an
    approved, non-placeholder catalog target record (catalog governance applies to IAM targets)."""
    for approved in ({}, {"fixture": {**APPROVED_RUNTIME_B["fixture"], "placeholder": True}},
                     {"fixture": {**APPROVED_RUNTIME_B["fixture"], "endpoint": RUNTIME_B_ENDPOINT.replace("abc123", "other")}}):
        with pytest.raises(HTTPException) as excinfo:
            call_platform_metadata(monkeypatch, mcp_server_control(), discovered=matching_discovery(), approved=approved)
        assert excinfo.value.detail == "APPROVED_MCP_SERVER_TARGET_REQUIRED"


# ---------------------------------------------------------------------------
# 2. GatewayMCP transport: gateway-domain pin + SigV4 on every RPC (this client only)
# ---------------------------------------------------------------------------

GATEWAY_URL = "https://abc123synthetic.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp"


def synthetic_session():
    return SimpleNamespace(region_name="us-west-2",
                           get_credentials=lambda: Credentials("synthetic-key", "synthetic-secret"))


def test_gateway_mcp_client_rejects_non_gateway_endpoints():
    """GatewayMCP -- this client class -- refuses to be constructed against endpoints that are
    not a same-region Gateway domain. This proves only the client's own input validation; it
    does not prove that no other code path in the runtime could reach such endpoints."""
    for endpoint in ("https://synthetic.runtime.bedrock-agentcore.us-west-2.amazonaws.com/mcp",
                     "https://abc.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
                     "https://example.invalid/mcp"):
        with pytest.raises(ValueError):
            GatewayMCP(synthetic_session(), endpoint)


def test_discovery_and_call_are_sigv4_signed_for_any_target_type(monkeypatch):
    """The Runtime-side tool loop is target-type agnostic: initialize/tools/list/tools/call
    against the Gateway work identically whether the target is lambda or mcpServer, and every
    request this client sends carries a SigV4 signature for the bedrock-agentcore service."""
    seen = []

    def handler(request):
        seen.append(request)
        body = json.loads(request.content)
        if "id" not in body:  # notifications/initialized
            return httpx.Response(202)
        results = {
            "initialize": {"capabilities": {"tools": {}}, "serverInfo": {"name": "synthetic"}},
            "tools/list": {"tools": [{"name": "specialist___analyze",
                                      "description": "Synthetic specialist behind the Gateway.",
                                      "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}}]},
            "tools/call": {"content": [{"type": "text", "text": "synthetic specialist verdict"}]},
        }
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": results[body["method"]]})

    real_client = httpx.Client
    monkeypatch.setattr(journey_mcp.httpx, "Client",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    client = GatewayMCP(synthetic_session(), GATEWAY_URL)
    tools = client.discover()
    assert [tool["name"] for tool in tools] == ["specialist___analyze"]
    assert client.call("specialist___analyze", {"q": "plate reading dispute"}) == "synthetic specialist verdict"
    assert len(seen) >= 4  # initialize, initialized notification, tools/list, tools/call
    for request in seen:
        authorization = request.headers.get("Authorization", "")
        assert authorization.startswith("AWS4-HMAC-SHA256"), "every Gateway RPC must be SigV4 signed"
        assert re.search(r"/us-west-2/bedrock-agentcore/aws4_request", authorization)


# ---------------------------------------------------------------------------
# 3. IAM: GatewayRole cannot reach a Runtime-B target yet
# ---------------------------------------------------------------------------

def test_gateway_role_lacks_invoke_agent_runtime_today():
    """GAP PIN (flips when implemented): the journey Gateway execution role has no
    bedrock-agentcore:InvokeAgentRuntime statement, so an mcpServer target pointing at a
    Runtime B cannot be IAM-signed outbound by this Gateway today. Owner slice: add a scoped
    InvokeAgentRuntime statement for the specialist runtime ARN prefix to infra/journey.py."""
    from infra.journey import template
    body = template("arn:aws:bedrock-agentcore:us-west-2:" + ACCOUNT + ":token-vault/default/apikeycredentialprovider/synthetic",
                    "arn:aws:secretsmanager:us-west-2:" + ACCOUNT + ":secret:synthetic")
    statements = body["Resources"]["GatewayRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    actions = [action for statement in statements
               for action in (statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]])]
    assert "lambda:InvokeFunction" in actions
    assert "bedrock-agentcore:InvokeAgentRuntime" not in actions
