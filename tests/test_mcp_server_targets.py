import json
import sqlite3

import pytest
from fastapi import HTTPException

from backend.app import approved_targets
from backend.catalog import COMPONENTS
from backend.foundation_approval import tool_target, verify_tool_target
from backend.repository import SQLiteRepository
from .conftest import login, create, enqueue, finish
from .test_two_tool_patterns import AGENT_TOOL, SNOWFLAKE, set_grant, with_tool

ENDPOINT = "https://synthetic-gateway.example.invalid/mcp"
MCP_TARGET = {"type": "mcpServer", "name": "synthetic-mcp", "endpoint": "https://synthetic-mcp.example.invalid/mcp", "outbound_auth": "OAUTH"}
LAMBDA_TARGET = {"type": "lambda", "name": "synthetic-fn", "lambda_arn": "arn:aws:lambda:us-west-2:000000000000:function:synthetic-fn", "outbound_auth": "GATEWAY_IAM_ROLE"}
TOOL = {"name": "synthetic-mcp___lookup", "endpoint": ENDPOINT, "inputSchema": {"type": "object"}}
RUNTIME_TARGET = {"type": "mcpServer", "name": "synthetic-runtime", "outbound_auth": "GATEWAY_IAM_ROLE",
                  "endpoint": "https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-west-2%3A000000000000%3Aruntime%2Fsynthetic-abc/invocations?qualifier=DEFAULT"}


def discover(url):
    return [{"name": TOOL["name"], "inputSchema": TOOL["inputSchema"]}]


def component(component_id):
    return next(c for c in COMPONENTS if c["id"] == component_id)


def mcp_detail(endpoint=MCP_TARGET["endpoint"], auth="OAUTH", name="synthetic-mcp"):
    return {"name": name, "targetConfiguration": {"mcp": {"mcpServer": {"endpoint": endpoint}}},
            "credentialProviderConfigurations": [{"credentialProviderType": auth}]}


def test_specialist_is_registered_as_admin_owned_platform_curated_mcp_server_target():
    specialist = component(AGENT_TOOL)
    assert specialist["curation"] == "platform-curated" and specialist["owner"] == "platform-admin"
    target = tool_target(specialist)
    assert target["type"] == "mcpServer" and target["outbound_auth"] == "OAUTH"
    # The real Gateway endpoint is a placeholder until cloud; .invalid never resolves.
    assert target["placeholder"] is True and target["endpoint"].endswith(".placeholder.invalid/mcp")
    assert tool_target(component(SNOWFLAKE))["type"] == "mcpServer"
    # Local-only tools have no Gateway target at all.
    assert tool_target(component("synthetic-search")) is None


@pytest.mark.parametrize("target", [MCP_TARGET, LAMBDA_TARGET, {**MCP_TARGET, "outbound_auth": "API_KEY"}, RUNTIME_TARGET])
def test_lambda_and_mcp_server_targets_are_both_supported(target):
    assert tool_target({"target": target}) == target


@pytest.mark.parametrize("target", [
    {**MCP_TARGET, "type": "openApiSchema"},
    {**MCP_TARGET, "endpoint": "http://synthetic-mcp.example.invalid/mcp"},
    {**MCP_TARGET, "endpoint": "https://user:pw@synthetic-mcp.example.invalid/mcp"},
    {**MCP_TARGET, "endpoint": "https://synthetic-mcp.example.invalid/mcp?token=x"},
    {**MCP_TARGET, "outbound_auth": "NONE"},
    {**MCP_TARGET, "name": "bad name"},
    {**MCP_TARGET, "lambda_arn": LAMBDA_TARGET["lambda_arn"]},
    {**MCP_TARGET, "placeholder": "yes"},
    {**LAMBDA_TARGET, "outbound_auth": "OAUTH"},
    {**LAMBDA_TARGET, "lambda_arn": "arn:aws:lambda:us-west-2:1:function:x"},
    # IAM SigV4 outbound is only for a same-region AgentCore Runtime MCP endpoint.
    {**MCP_TARGET, "outbound_auth": "GATEWAY_IAM_ROLE"},
    {**RUNTIME_TARGET, "endpoint": RUNTIME_TARGET["endpoint"].replace("us-west-2", "us-east-1")},
    "mcpServer",
])
def test_malformed_targets_fail_closed(target):
    with pytest.raises(HTTPException, match="SUPPORTED_TOOL_TARGET_REQUIRED"):
        tool_target({"target": target})


def test_mcp_server_capability_is_approved_granted_pinned_and_resolved_like_other_tools(app, client, payload, tmp_path):
    login(client)
    item = client.get(f"/api/catalog/{AGENT_TOOL}").json()
    assert item["approved"] and item["requestable"] and not item["usable"]
    # Granted + pinned: the same grant and version-pin checks apply.
    set_grant(client, AGENT_TOOL, True)
    unpinned = with_tool(dict(payload, tools=list(payload["tools"]), component_versions=dict(payload["component_versions"])), AGENT_TOOL)
    unpinned["component_versions"][AGENT_TOOL] = "2"
    assert client.post("/api/agents", json=unpinned).status_code == 409
    definition = create(client, with_tool(payload, AGENT_TOOL))
    assert finish(app, client, enqueue(client, definition))["stage"] == "PASS"
    # Resolved: the governance path resolves the approved mcpServer target record.
    with sqlite3.connect(tmp_path / "test.sqlite") as db:
        db.row_factory = sqlite3.Row
        assert approved_targets(SQLiteRepository(db), [AGENT_TOOL, "synthetic-search"]) == {"risk-analyst-specialist": tool_target(component(AGENT_TOOL))}
    path = f"/api/agents/{definition['agent_id']}/invoke"
    assert client.post(path, json={"version": 1, "input": "What is the renewal risk?"}).status_code == 200
    # A target that drifts to an unsupported shape blocks later calls (every invoke re-resolves it).
    with sqlite3.connect(tmp_path / "test.sqlite") as db:
        original = db.execute("SELECT body FROM components WHERE id=?", (AGENT_TOOL,)).fetchone()[0]
        body = json.loads(original)
        body["target"] = {**body["target"], "endpoint": "http://risk-analyst-specialist.placeholder.invalid/mcp"}
        db.execute("UPDATE components SET body=? WHERE id=?", (json.dumps(body), AGENT_TOOL))
    denied = client.post(path, json={"version": 1, "input": "What is the renewal risk?"})
    assert denied.status_code == 409 and denied.json()["detail"] == "SUPPORTED_TOOL_TARGET_REQUIRED"
    with sqlite3.connect(tmp_path / "test.sqlite") as db:
        db.execute("UPDATE components SET body=? WHERE id=?", (original, AGENT_TOOL))
    assert client.post(path, json={"version": 1, "input": "What is the renewal risk?"}).status_code == 200
    # Approved: withdrawing catalog approval blocks it like any other component.
    login(client, "admin")
    assert client.post(f"/api/admin/catalog/components/{AGENT_TOOL}", json={"approved": False}).status_code == 200
    login(client)
    assert client.post(path, json={"version": 1, "input": "What is the renewal risk?"}).status_code == 403
    assert client.get(f"/api/catalog/{AGENT_TOOL}").status_code == 404


def test_lambda_target_verification_is_unchanged():
    detail = {"name": "synthetic-fn", "targetConfiguration": {"mcp": {"lambda": {"toolSchema": {"inlinePayload": [{"name": "lookup", "inputSchema": {"type": "object"}}]}}}}}
    tool = {**TOOL, "name": "synthetic-fn___lookup"}
    verify_tool_target(detail, [tool], ENDPOINT, {})
    with pytest.raises(HTTPException, match="TOOL_TARGET_SCHEMA_REQUIRED"):
        verify_tool_target(detail, [{**tool, "inputSchema": {"type": "object", "properties": {}}}], ENDPOINT, {})


def test_mcp_server_gateway_target_must_match_an_approved_catalog_record():
    approved = {"synthetic-mcp": MCP_TARGET}
    verify_tool_target(mcp_detail(), [TOOL], ENDPOINT, approved, discover=discover)
    with pytest.raises(HTTPException, match="TOOL_TARGET_SCHEMA_REQUIRED"):
        verify_tool_target(mcp_detail(), [{**TOOL, "name": "other___lookup"}], ENDPOINT, approved, discover=discover)
    # No inline schema: live discovery is required and must match exactly.
    with pytest.raises(HTTPException, match="TOOL_TARGET_DISCOVERY_REQUIRED"):
        verify_tool_target(mcp_detail(), [TOOL], ENDPOINT, approved)
    with pytest.raises(HTTPException, match="TOOL_TARGET_SCHEMA_REQUIRED"):
        verify_tool_target(mcp_detail(), [TOOL], ENDPOINT, approved, discover=lambda url: [])


@pytest.mark.parametrize("detail,approved", [
    (mcp_detail(), {}),
    (mcp_detail(), {"synthetic-mcp": {**MCP_TARGET, "placeholder": True}}),
    (mcp_detail(endpoint="https://drifted.example.invalid/mcp"), {"synthetic-mcp": MCP_TARGET}),
    (mcp_detail(auth="API_KEY"), {"synthetic-mcp": MCP_TARGET}),
    (mcp_detail(name="unapproved"), {"synthetic-mcp": MCP_TARGET}),
])
def test_unapproved_placeholder_or_drifted_mcp_server_targets_fail_closed(detail, approved):
    with pytest.raises(HTTPException, match="APPROVED_MCP_SERVER_TARGET_REQUIRED"):
        verify_tool_target(detail, [TOOL], ENDPOINT, approved, discover=discover)


def test_unknown_target_shape_fails_closed():
    detail = {"name": "synthetic-mcp", "targetConfiguration": {"mcp": {"openApiSchema": {}}}}
    with pytest.raises(HTTPException, match="TOOL_TARGET_SCHEMA_REQUIRED"):
        verify_tool_target(detail, [TOOL], ENDPOINT, {"synthetic-mcp": MCP_TARGET}, discover=discover)
