import pytest

from backend.harness import ToolDenied, call_tool, run_case
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
    assert {"type": "tools_call", "calls": [{"tool": AGENT_TOOL, "source": "agent:synthetic-risk-analyst@1"}]} in body["trace"]
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
    assert {"type": "tools_call", "calls": [{"tool": AGENT_TOOL, "denied": "AGENT_VERSION_NOT_PINNED"}]} in result["trace"]
