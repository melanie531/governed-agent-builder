import sqlite3

import pytest

from backend.harness import READ_VIEW, ToolDenied, call_tool, tool_scope
from .conftest import login, create, enqueue, finish
from .test_two_tool_patterns import AGENT_TOOL, SNOWFLAKE, MANIFEST, set_grant, with_tool

INCIDENTS = "SYNTHETIC_DB.APPROVED_VIEWS.OPEN_INCIDENTS_V"
HEALTH = "SYNTHETIC_DB.APPROVED_VIEWS.ACCOUNT_HEALTH_V"
QUESTION = {"question": "What is the renewal risk?"}


def grant(client, component_id, scope=None, persona="alex"):
    login(client, "admin")
    body = {"persona_id": persona, "component_id": component_id, "enabled": True}
    response = client.post("/api/admin/grants", json={**body, "scope": scope} if scope is not None else body)
    login(client)
    return response


def tools_call(body):
    return next(t for t in body["trace"] if t["type"] == "tools_call")["calls"]


def passed(app, client, payload, component_id):
    definition = create(client, with_tool(payload, component_id))
    assert finish(app, client, enqueue(client, definition))["stage"] == "PASS"
    return f"/api/agents/{definition['agent_id']}/invoke"


def test_declared_scopes_cover_exactly_what_each_tool_can_reach():
    assert tool_scope(AGENT_TOOL) == {"operations": [READ_VIEW, "risk_review"], "data": [INCIDENTS]}
    assert tool_scope(SNOWFLAKE) == {"operations": [READ_VIEW], "data": [HEALTH, INCIDENTS]}
    assert tool_scope("synthetic-search") is None


@pytest.mark.parametrize("scope,code", [
    (None, "CALLER_SCOPE_REQUIRED"),
    # Narrow data: the specialist's own manifest allows the incidents view; the caller's grant does not.
    ({"operations": ["risk_review", READ_VIEW], "data": []}, "CALLER_DATA_OUT_OF_SCOPE"),
    ({"operations": ["risk_review", READ_VIEW], "data": [HEALTH]}, "CALLER_DATA_OUT_OF_SCOPE"),
    # Narrow operations: the caller may not have the specialist read views, or run a risk review at all.
    ({"operations": ["risk_review"], "data": [INCIDENTS]}, "CALLER_OPERATION_OUT_OF_SCOPE"),
    ({"operations": [READ_VIEW], "data": [INCIDENTS]}, "CALLER_OPERATION_OUT_OF_SCOPE"),
])
def test_specialist_cannot_reach_ops_or_data_outside_the_callers_scope(scope, code):
    caller = {"tools": [AGENT_TOOL], "component_versions": {AGENT_TOOL: "1"}}
    with pytest.raises(ToolDenied, match=code):
        call_tool(caller, AGENT_TOOL, dict(QUESTION), scope)


def test_specialist_runs_when_its_work_is_within_the_callers_scope():
    caller = {"tools": [AGENT_TOOL], "component_versions": {AGENT_TOOL: "1"}}
    result = call_tool(caller, AGENT_TOOL, dict(QUESTION), {"operations": ["risk_review", READ_VIEW], "data": [INCIDENTS]})
    assert result["inner_calls"][0]["source"] == "snowflake:open_incident_counts"


def test_snowflake_call_is_confined_to_the_callers_data_scope():
    scope = {"operations": [READ_VIEW], "data": [HEALTH]}
    assert call_tool(MANIFEST, SNOWFLAKE, {"query_id": "account_health_summary"}, scope)["view"] == HEALTH
    with pytest.raises(ToolDenied, match="CALLER_DATA_OUT_OF_SCOPE"):
        call_tool(MANIFEST, SNOWFLAKE, {"query_id": "open_incident_counts"}, scope)
    with pytest.raises(ToolDenied, match="CALLER_OPERATION_OUT_OF_SCOPE"):
        call_tool(MANIFEST, SNOWFLAKE, {"query_id": "account_health_summary"}, {"operations": [], "data": [HEALTH]})


def test_scope_cannot_be_smuggled_through_tool_arguments_or_the_definition(client, payload):
    with pytest.raises(ToolDenied, match="AGENT_TOOL_ARGUMENTS_REJECTED"):
        call_tool(MANIFEST, AGENT_TOOL, {**QUESTION, "scope": tool_scope(AGENT_TOOL)}, None)
    assert grant(client, AGENT_TOOL).status_code == 200
    forged = with_tool(payload, AGENT_TOOL)
    forged["scope"] = tool_scope(AGENT_TOOL)
    assert client.post("/api/agents", json=forged).status_code == 422


def test_server_enforces_narrow_caller_grant_scope_on_invoke(app, client, payload):
    assert grant(client, AGENT_TOOL, {"operations": ["risk_review", READ_VIEW], "data": []}).status_code == 200
    path = passed(app, client, payload, AGENT_TOOL)
    body = client.post(path, json={"version": 1, "input": QUESTION["question"]}).json()
    # Denied server-side and recorded; the specialist's result is never returned.
    assert body["sources"] == [] and "Synthetic risk review" not in body["output"]
    assert tools_call(body) == [{"tool": AGENT_TOOL, "via": "mcp-tool-via-gateway", "denied": "CALLER_DATA_OUT_OF_SCOPE"}]
    # Re-granting with the incidents view in scope allows the same call.
    assert grant(client, AGENT_TOOL, {"operations": ["risk_review", READ_VIEW], "data": [INCIDENTS]}).status_code == 200
    body = client.post(path, json={"version": 1, "input": QUESTION["question"]}).json()
    assert body["sources"] == ["agent:synthetic-risk-analyst@1"]


def test_snowflake_invoke_is_confined_to_the_callers_granted_views(app, client, payload):
    assert grant(client, SNOWFLAKE, {"operations": [READ_VIEW], "data": [HEALTH]}).status_code == 200
    path = passed(app, client, payload, SNOWFLAKE)
    assert client.post(path, json={"version": 1, "input": "Show account health"}).json()["sources"] == ["snowflake:account_health_summary"]
    body = client.post(path, json={"version": 1, "input": "Show open incident counts"}).json()
    assert body["sources"] == [] and tools_call(body)[0]["denied"] == "CALLER_DATA_OUT_OF_SCOPE"


@pytest.mark.parametrize("component_id,scope", [
    (AGENT_TOOL, {"operations": ["risk_review", "delete_account"], "data": [INCIDENTS]}),
    (AGENT_TOOL, {"operations": ["risk_review"], "data": ["SYNTHETIC_DB.RAW.CUSTOMERS"]}),
    ("restricted-insights", {"operations": [], "data": []}),
])
def test_admin_grant_scope_may_only_narrow_the_declared_scope(client, component_id, scope):
    assert grant(client, component_id, scope).status_code == 422
    # The rejected grant is rolled back as a whole.
    assert not client.get(f"/api/catalog/{component_id}").json()["granted"]


def test_revocation_drops_scope_and_approved_request_gets_the_declared_scope(app, client, payload):
    assert grant(client, AGENT_TOOL, {"operations": ["risk_review"], "data": []}).status_code == 200
    set_grant(client, AGENT_TOOL, False)
    request = client.post("/api/requests", json={"component_id": AGENT_TOOL, "reason": "Need governed risk reviews"})
    assert request.status_code == 201, request.text
    login(client, "admin")
    decision = client.post(f"/api/admin/requests/{request.json()['id']}/decision", json={"approve": True, "reason": "Approved risk reviews"})
    assert decision.status_code == 200, decision.text
    login(client)
    body = client.post(passed(app, client, payload, AGENT_TOOL), json={"version": 1, "input": QUESTION["question"]}).json()
    assert body["sources"] == ["agent:synthetic-risk-analyst@1"]


def test_grant_without_stored_scope_fails_closed(app, client, payload, tmp_path):
    assert grant(client, AGENT_TOOL).status_code == 200
    path = passed(app, client, payload, AGENT_TOOL)
    with sqlite3.connect(tmp_path / "test.sqlite") as db:
        db.execute("DELETE FROM settings WHERE key LIKE 'caller-scope:%'")
    body = client.post(path, json={"version": 1, "input": QUESTION["question"]}).json()
    assert tools_call(body)[0]["denied"] == "CALLER_SCOPE_REQUIRED"
