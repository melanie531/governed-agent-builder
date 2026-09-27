"""MCP SDK server + signed exchange adapter + real Journey authority, all offline."""
import json

from starlette.testclient import TestClient

from backend import alpr, journey_alpr
from foundation_harness.alpr_exchange import CALLER_HEADER, SpecialistExchange
from runtime.mcp_specialist.alpr_live import LiveALPR, create_app
from tests.test_alpr_transport_admission import CONFIG
from tests.test_journey_alpr_admission import setup, issue, PRINCIPAL, ARGS, TOOL


def test_live_mcp_redemption_and_revocation_use_current_journey_authority(setup, monkeypatch):
    # The fixture's issuer proof and this fixed IAM principal are test adapters;
    # no test headers or business arguments are used as workload identity.
    from types import SimpleNamespace
    monkeypatch.setenv("ALPR_VIEW_SOURCE", "live")
    monkeypatch.setenv("ALPR_SNOWFLAKE_SSM_PREFIX", "/governed-agent-builder/alpr")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    journey, _ = setup
    exchange = SpecialistExchange(SimpleNamespace(region_name="us-west-2"), {**CONFIG, "deployment_digest": "b" * 64})
    requests = []
    def send(url, raw, headers, timeout, service):
        body = json.loads(raw)
        requests.append(body["operation"])
        with journey.store.tx() as db:
            return journey_alpr.exchange(db, journey, principal_arn=PRINCIPAL, body=body), {}
    monkeypatch.setattr(exchange.transport, "send", send)
    queries = []
    def rows(query, case):
        queries.append((query, case))
        alpr._query_ids.set(({"query_id": query, "case_id": case,
            "view": alpr.VIEWS[query][0], "snowflake_query_id": "offline-" + query},))
        return []
    monkeypatch.setattr(alpr, "live_rows", rows)
    app = create_app(LiveALPR(exchange))
    ref = issue(setup)
    with TestClient(app) as client:
        def rpc(method, params, reference=None):
            headers = {"accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-03-26"}
            if reference:
                headers[CALLER_HEADER] = reference
            response = client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            assert response.status_code == 200
            return response.json()
        assert "error" in rpc("tools/list", {})
        listed = rpc("tools/list", {}, ref)["result"]["tools"]
        assert [tool["name"] for tool in listed] == [TOOL]
        result = rpc("tools/call", {"name": TOOL, "arguments": ARGS}, ref)["result"]
        assert not result.get("isError") and result["structuredContent"]["read_only"]
        assert queries == [(query, "ALPR-C001") for query in alpr.VIEWS]
        assert requests.count("redeem") == 1 and requests.count("view") == 4 and requests[-1] == "finish"
        assert rpc("tools/call", {"name": TOOL, "arguments": ARGS}, ref)["result"]["isError"]
        second = issue(setup)
        with journey.store.tx() as db:
            db.delete("grants", where=[("persona", "=", "alex"), ("component", "=", TOOL)])
        assert rpc("tools/call", {"name": TOOL, "arguments": ARGS}, second)["result"]["isError"]
        assert len(queries) == 4
