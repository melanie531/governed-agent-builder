"""Offline live-runtime boundaries; no sockets, credentials or Snowflake access."""
import time

import pytest
from starlette.testclient import TestClient

from backend import alpr, harness
from foundation_harness.config import digest
from runtime.mcp_specialist import alpr_live

TOOL = "agent-alpr-remediation"
ARGS = {"question": "Investigate ALPR-C001"}
REFERENCE = "a" * 43


class Exchange:
    def __init__(self):
        self.calls = []
        self.revoked = False

    def list_tools(self, request):
        # Only this offline adapter supplies the platform's listing authority.
        return list(alpr_live.TOOLS)

    def request(self, reference, operation, **extra):
        self.calls.append((operation, extra))
        if self.revoked:
            raise harness.ToolDenied("ALPR_ADMISSION_DENIED")
        return {"reference": reference, "binding_digest": "b" * 64,
                "expires_at": time.time() + 60, "tool": TOOL, "version": "1",
                "arguments_digest": digest(ARGS), "scope": harness.tool_scope(TOOL)}


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("ALPR_VIEW_SOURCE", "live")
    monkeypatch.setenv("ALPR_SNOWFLAKE_SSM_PREFIX", "/governed-agent-builder/alpr")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    exchange = Exchange()
    runtime = alpr_live.LiveALPR(exchange)
    reads = []

    def query(query_id, case_id):
        reads.append(query_id)
        alpr._query_ids.set(({"query_id": query_id, "case_id": case_id,
                             "view": alpr.VIEWS[query_id][0], "snowflake_query_id": "offline-" + query_id},))
        return []

    monkeypatch.setattr(alpr, "live_rows", query)
    monkeypatch.setattr(alpr.SNAPSHOT.__class__, "read_text", lambda *a, **k: pytest.fail("snapshot read"))
    return runtime, exchange, reads


def test_live_call_uses_all_four_views_and_rechecks_each(live):
    runtime, exchange, reads = live
    result = runtime.call(REFERENCE, TOOL, ARGS)
    assert reads == list(alpr.VIEWS)
    assert [op for op, _ in exchange.calls] == ["redeem", "authorize", *["view"] * 4, "finish"]
    assert result["read_only"] and result["actions_executed"] == []
    assert all(inner["snowflake_query_ids"] for inner in result["inner_calls"])
    assert result["synthetic"] is True


@pytest.mark.parametrize("reference", [None, "", "admin", ["a"], "a" * 42])
def test_missing_or_invalid_context_never_queries(live, reference):
    runtime, exchange, reads = live
    with pytest.raises(harness.ToolDenied, match="ALPR_CALLER_REFERENCE_REQUIRED"):
        runtime.call(reference, TOOL, ARGS)
    assert not exchange.calls and not reads


@pytest.mark.parametrize("spoof", [{"role": "admin"}, {"scope": {}}, {"sql": "SELECT 1"}])
def test_business_arguments_remain_question_only(live, spoof):
    runtime, exchange, reads = live
    with pytest.raises(harness.ToolDenied, match="AGENT_TOOL_ARGUMENTS_REJECTED"):
        runtime.call(REFERENCE, TOOL, {**ARGS, **spoof})
    assert not exchange.calls and not reads


def test_revocation_between_inner_reads_stops_dispatch(live, monkeypatch):
    runtime, exchange, reads = live
    original = alpr.live_rows

    def revoke(query, case):
        rows = original(query, case)
        exchange.revoked = True
        return rows

    monkeypatch.setattr(alpr, "live_rows", revoke)
    with pytest.raises(harness.ToolDenied, match="ALPR_ADMISSION_DENIED"):
        runtime.call(REFERENCE, TOOL, ARGS)
    assert reads == ["ownership_at_event"] and alpr.consume_query_ids() == []


@pytest.mark.parametrize("field,value", [
    ("version", "2"), ("tool", "agent-risk-analyst"), ("arguments_digest", "c" * 64),
    ("expires_at", 0), ("reference", "c" * 43), ("expires_at", float("nan")),
])
def test_wrong_redemption_binding_is_denied(live, field, value):
    runtime, exchange, reads = live
    original = exchange.request
    exchange.request = lambda *a, **k: {**original(*a, **k), field: value}
    with pytest.raises(harness.ToolDenied):
        runtime.call(REFERENCE, TOOL, ARGS)
    assert not reads


@pytest.mark.parametrize("key", ["ALPR_VIEW_SOURCE", "ALPR_SNOWFLAKE_SSM_PREFIX", "AWS_REGION"])
def test_absent_live_configuration_fails_closed(live, monkeypatch, key):
    runtime, exchange, reads = live
    monkeypatch.delenv(key)
    with pytest.raises(harness.ToolDenied, match="ALPR_LIVE_CONFIG_REQUIRED"):
        runtime.call(REFERENCE, TOOL, ARGS)
    assert not reads


def test_live_entry_cannot_start_with_demo_authority(live, monkeypatch):
    monkeypatch.setenv("CALLER_SCOPE_PROFILE", "full")
    with pytest.raises(harness.ToolDenied, match="ALPR_DEMO_AUTHORITY_FORBIDDEN"):
        alpr_live.create_app(live[0])


def test_production_entry_requires_verified_channel_and_exchange(monkeypatch):
    monkeypatch.delenv("ALPR_LIVE_CONFIG", raising=False)
    with pytest.raises(harness.ToolDenied, match="ALPR_LIVE_CONFIG_REQUIRED"):
        alpr_live.configured_runtime()


def test_source_change_between_reads_has_no_snapshot_fallback(live, monkeypatch):
    runtime, _, reads = live
    original = alpr.live_rows
    def switch(query, case):
        result = original(query, case)
        monkeypatch.setenv("ALPR_VIEW_SOURCE", "snapshot")
        return result
    monkeypatch.setattr(alpr, "live_rows", switch)
    with pytest.raises(harness.ToolDenied, match="ALPR_LIVE_CONFIG_REQUIRED"):
        runtime.call(REFERENCE, TOOL, ARGS)
    assert reads == ["ownership_at_event"]


def test_expired_call_budget_prevents_query_dispatch(live, monkeypatch):
    runtime, exchange, reads = live
    original = exchange.request
    now = [100.0]
    monkeypatch.setattr(alpr.time, "monotonic", lambda: now[0])
    def delayed(*args, **kwargs):
        result = original(*args, **kwargs)
        now[0] += 11
        return result
    exchange.request = delayed
    with pytest.raises(harness.ToolDenied, match="ALPR_CALL_DEADLINE_EXCEEDED"):
        runtime.call(REFERENCE, TOOL, ARGS)
    assert not reads


def test_no_successful_live_response_without_query_id(live, monkeypatch):
    runtime, _, reads = live
    monkeypatch.setattr(alpr, "live_rows", lambda *a: [])
    with pytest.raises(harness.ToolDenied, match="ALPR_LIVE_QUERY_EVIDENCE_REQUIRED"):
        runtime.call(REFERENCE, TOOL, ARGS)


def test_no_data_scope_cannot_be_widened_by_question(live):
    runtime, exchange, reads = live
    original = exchange.request
    exchange.request = lambda *a, **k: {**original(*a, **k),
        "scope": {"operations": harness.tool_scope(TOOL)["operations"], "data": []}}
    with pytest.raises(harness.ToolDenied, match="CALLER_DATA_OUT_OF_SCOPE"):
        runtime.call(REFERENCE, TOOL, ARGS)
    assert not reads


def test_demo_entry_refuses_live_authority(monkeypatch):
    from runtime.mcp_specialist.agentcore import deployment_caller
    monkeypatch.setenv("ALPR_VIEW_SOURCE", "live")
    with pytest.raises(harness.ToolDenied, match="DEMO_AUTHORITY_FORBIDDEN_IN_LIVE"):
        deployment_caller(None)


def test_mcp_protocol_in_process_question_only_and_missing_context(live):
    runtime, _, reads = live
    app = alpr_live.create_app(runtime)
    headers = {"accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-03-26"}
    with TestClient(app) as client:
        def rpc(method, params):
            response = client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            assert response.status_code == 200
            return response.json()
        assert "tools" in rpc("initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "offline", "version": "1"}})["result"]["capabilities"]
        tools = rpc("tools/list", {})["result"]["tools"]
        assert {tool["name"] for tool in tools} == set(alpr_live.TOOLS)
        assert all(set(tool["inputSchema"]["properties"]) == {"question"}
                   and tool["inputSchema"]["additionalProperties"] is False for tool in tools)
        denied = rpc("tools/call", {"name": TOOL, "arguments": ARGS})["result"]
        assert denied["isError"] and "ALPR_CALLER_REFERENCE_REQUIRED" in denied["content"][0]["text"]
        assert not reads
        headers[alpr_live.CALLER_HEADER] = REFERENCE
        allowed = rpc("tools/call", {"name": TOOL, "arguments": ARGS})["result"]
        assert not allowed.get("isError") and allowed["structuredContent"]["read_only"]
