import json

import pytest

from backend import harness
from backend.harness import ToolDenied, call_tool, list_tools, run_case
from .conftest import login, create, enqueue, finish

AGENT_TOOL, SNOWFLAKE = "agent-risk-analyst", "snowflake-approved-views"


def set_grant(client, component_id, enabled):
    login(client, "admin")
    response = client.post("/api/admin/grants", json={"persona_id": "alex", "component_id": component_id, "enabled": enabled})
    assert response.status_code == 200, response.text
    login(client)


def with_tool(payload, component_id):
    payload["tools"].append(component_id)
    payload["component_versions"][component_id] = "1"
    return payload


def passed_agent(app, client, payload, component_id):
    set_grant(client, component_id, True)
    definition = create(client, with_tool(payload, component_id))
    assert finish(app, client, enqueue(client, definition))["stage"] == "PASS"
    return definition


@pytest.mark.parametrize("component_id", [AGENT_TOOL, SNOWFLAKE])
def test_both_patterns_are_catalog_tools_gated_by_grants(client, payload, component_id):
    login(client)
    item = client.get(f"/api/catalog/{component_id}").json()
    assert item["kind"] == "tool" and item["requestable"] and not item["usable"]
    tools = {t["id"] for t in client.get("/api/build-options?foundation_id=research").json()["choices"]["tools"]}
    assert component_id not in tools
    assert client.post("/api/agents", json=with_tool(payload, component_id)).status_code == 403
    set_grant(client, component_id, True)
    assert client.get(f"/api/catalog/{component_id}").json()["usable"]
    tools = {t["id"]: t for t in client.get("/api/build-options?foundation_id=research").json()["choices"]["tools"]}
    assert tools[component_id]["use_when"]
    # Only the upper-level Research foundation composes these tools.
    assert component_id not in {t["id"] for t in client.get("/api/build-options?foundation_id=knowledge").json()["choices"]["tools"]}


def test_agent_as_tool_invokes_until_grant_is_revoked(app, client, payload):
    login(client)
    definition = passed_agent(app, client, payload, AGENT_TOOL)
    path = f"/api/agents/{definition['agent_id']}/invoke"
    result = client.post(path, json={"version": 1, "input": "What is the renewal risk for this account?"})
    assert result.status_code == 200, result.text
    body = result.json()
    assert "Synthetic risk review" in body["output"]
    assert body["sources"] == ["agent:synthetic-risk-analyst@1"]
    [call] = next(t for t in body["trace"] if t["type"] == "tools_call")["calls"]
    assert call["tool"] == AGENT_TOOL and call["via"] == "mcp-tool-via-gateway" and call["source"] == "agent:synthetic-risk-analyst@1"
    set_grant(client, AGENT_TOOL, False)
    assert client.post(path, json={"version": 1, "input": "What is the renewal risk?"}).status_code == 403


def test_snowflake_connector_returns_masked_rows_through_governed_invoke(app, client, payload):
    login(client)
    definition = passed_agent(app, client, payload, SNOWFLAKE)
    body = client.post(f"/api/agents/{definition['agent_id']}/invoke", json={"version": 1, "input": "Show account health"}).json()
    assert body["sources"] == ["snowflake:account_health_summary"] and "masked synthetic rows" in body["output"]


MANIFEST = {"tools": [AGENT_TOOL, SNOWFLAKE], "component_versions": {AGENT_TOOL: "1", SNOWFLAKE: "1"}}


@pytest.mark.parametrize("arguments", [
    {"question": "risk?", "role": "admin"},
    {"question": "risk?", "tenant_id": "other-tenant"},
    {"question": "risk?", "agent_version": "2"},
    {"prompt": "risk?"},
])
def test_agent_tool_rejects_model_supplied_identity_or_extra_arguments(arguments):
    with pytest.raises(ToolDenied, match="AGENT_TOOL_ARGUMENTS_REJECTED"):
        call_tool(MANIFEST, AGENT_TOOL, arguments)


def test_tools_call_requires_manifest_selection_and_pinned_version():
    with pytest.raises(ToolDenied, match="TOOL_NOT_IN_MANIFEST"):
        call_tool({"tools": [], "component_versions": {}}, AGENT_TOOL, {"question": "risk?"})
    with pytest.raises(ToolDenied, match="AGENT_VERSION_NOT_PINNED"):
        call_tool({"tools": [AGENT_TOOL], "component_versions": {AGENT_TOOL: "2"}}, AGENT_TOOL, {"question": "risk?"})
    with pytest.raises(ToolDenied, match="CONNECTOR_VERSION_NOT_PINNED"):
        call_tool({"tools": [SNOWFLAKE], "component_versions": {}}, SNOWFLAKE, {"query_id": "account_health_summary"})


@pytest.mark.parametrize("arguments", [
    {"sql": "SELECT * FROM SYNTHETIC_DB.RAW.CUSTOMERS"},
    {"execute_sql": "SELECT 1"},
    {"query_id": "SELECT * FROM SYNTHETIC_DB.RAW.CUSTOMERS"},
    {"query_id": "account_health_summary", "sql": "DROP TABLE SYNTHETIC_DB.APPROVED_VIEWS.ACCOUNT_HEALTH_V"},
    {"query_id": "unlisted_named_query"},
    "SELECT 1",
])
def test_snowflake_connector_rejects_generic_sql_and_unlisted_queries(arguments):
    with pytest.raises(ToolDenied, match="SNOWFLAKE_QUERY_NOT_WHITELISTED"):
        call_tool(MANIFEST, SNOWFLAKE, arguments)


def test_snowflake_whitelisted_query_returns_only_masked_synthetic_rows():
    result = call_tool(MANIFEST, SNOWFLAKE, {"query_id": "account_health_summary"})
    assert result["masked"] and result["view"].startswith("SYNTHETIC_DB.APPROVED_VIEWS.")
    assert all(row["contact_email"] == "***MASKED***" and row["account"].startswith("SYN-") for row in result["rows"])


def test_unpinned_agent_tool_is_denied_in_trace_not_executed():
    definition = {"prompt": "Cite sources.", "tools": [AGENT_TOOL], "component_versions": {AGENT_TOOL: "2"},
                  "skills": [], "output_format": "text"}
    result = run_case(definition, "What is the risk?")
    assert result["sources"] == []
    assert {"type": "tools_call", "calls": [{"tool": AGENT_TOOL, "via": "mcp-tool-via-gateway", "denied": "AGENT_VERSION_NOT_PINNED"}]} in result["trace"]


def test_specialist_is_a_platform_curated_mcp_tool_via_gateway_not_a2a(client):
    set_grant(client, AGENT_TOOL, True)
    tools = {t["id"]: t for t in client.get("/api/build-options?foundation_id=research").json()["choices"]["tools"]}
    item = tools[AGENT_TOOL]
    assert item["tool_type"] == "mcp-tool-via-gateway" and item["curation"] == "platform-curated"
    assert "MCP tool via Tool Gateway" in item["protocol"] and "a2a" not in json.dumps(item).lower()
    assert "Tool Gateway" in client.get(f"/api/catalog/{SNOWFLAKE}").json()["protocol"]
    # The upper agent discovers the specialist exactly like any other MCP tool (tools/list).
    listed = {t["name"]: t for t in list_tools({"tools": ["synthetic-search", AGENT_TOOL, SNOWFLAKE]})}
    assert set(listed) == {AGENT_TOOL, SNOWFLAKE} and all(t["via"] == "mcp-tool-via-gateway" for t in listed.values())
    assert listed[AGENT_TOOL]["inputSchema"] == {"type": "object", "properties": {"question": {"type": "string"}},
                                                 "required": ["question"], "additionalProperties": False}
    assert listed[SNOWFLAKE]["inputSchema"]["properties"]["query_id"]["enum"] == ["account_health_summary", "open_incident_counts"]
    assert list_tools({"tools": []}) == []


def test_builder_created_agent_is_directly_usable_without_becoming_a_specialist(app, client, payload):
    login(client)
    definition = create(client, payload)
    assert finish(app, client, enqueue(client, definition))["stage"] == "PASS"
    result = client.post(f"/api/agents/{definition['agent_id']}/invoke", json={"version": 1, "input": "What is the synthetic policy?"})
    assert result.status_code == 200, result.text
    # It is not a curated specialist and cannot be smuggled into another manifest as an MCP tool.
    assert definition["agent_id"] not in harness.SPECIALIST_AGENTS
    with pytest.raises(ToolDenied, match="TOOL_NOT_IN_MANIFEST"):
        call_tool({"tools": [definition["agent_id"]], "component_versions": {definition["agent_id"]: "1"}}, definition["agent_id"], {"question": "risk?"})
    other = dict(payload, name="Upper agent", tools=[*payload["tools"], definition["agent_id"]],
                 component_versions={**payload["component_versions"], definition["agent_id"]: "1"})
    assert client.post("/api/agents", json=other).status_code in (403, 404, 422)


def test_specialist_inner_tools_run_under_its_own_manifest_and_identity():
    caller = {"tools": [AGENT_TOOL], "component_versions": {AGENT_TOOL: "1"}}
    result = call_tool(caller, AGENT_TOOL, {"question": "risk?"})
    # The caller holds no Snowflake grant; the specialist's own manifest still governs its inner call.
    assert result["inner_calls"] == [{"tool": SNOWFLAKE, "via": "mcp-tool-via-gateway", "identity": "synthetic-svc-risk-analyst",
                                      "source": "snowflake:open_incident_counts"}]


def test_caller_grants_are_not_inherited_into_specialist_internals(monkeypatch):
    specialist = dict(harness.SPECIALIST_AGENTS[AGENT_TOOL], manifest={"tools": [], "component_versions": {}})
    monkeypatch.setitem(harness.SPECIALIST_AGENTS, AGENT_TOOL, specialist)
    # Even a caller that holds the inner tool cannot lend it to the specialist: fail closed.
    with pytest.raises(ToolDenied, match="SPECIALIST_INNER_TOOL_DENIED"):
        call_tool(MANIFEST, AGENT_TOOL, {"question": "risk?"})
