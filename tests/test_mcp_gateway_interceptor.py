import json
import time

import pytest

from backend.catalog import PERSONAS
from backend.store import Store
from tests.journey_support import seed
from backend.mcp_gateway_interceptor import intercept


@pytest.fixture
def store(tmp_path):
    store = Store(str(tmp_path / "gateway.sqlite"))
    seed(store)
    with store.tx() as db:
        db.execute("CREATE TABLE principals(id TEXT PRIMARY KEY, body TEXT, expires REAL)")
        db.insert("principals", {"id": "alex", "body": json.dumps(PERSONAS["alex"]), "expires": time.time() + 300})
        db.delete("grants", where=[("persona", "=", "alex"), ("component", "=", "web-search")])
    return store


def request(method, name=None):
    return {"interceptorInputVersion": "1.0", "mcp": {"gatewayRequest": {"body": {
        "jsonrpc": "2.0", "id": 1, "method": method, "params": {"name": name, "arguments": {"query": "test"}}}}}}


def test_gateway_lists_and_executes_only_current_workspace_grants(store):
    with store.tx() as db:
        listing = intercept(request("tools/list"), db, "alex", "test-gateway")
        tools = listing["mcp"]["transformedGatewayResponse"]["body"]["result"]["tools"]
        assert "knowledge___search" in [t["name"] for t in tools]
        assert "tavily___tavily_search" not in [t["name"] for t in tools]
        allowed = intercept(request("tools/call", "knowledge___search"), db, "alex", "test-gateway")
        assert "transformedGatewayRequest" in allowed["mcp"]
        denied = intercept(request("tools/call", "tavily___tavily_search"), db, "alex", "test-gateway")
        assert denied["mcp"]["transformedGatewayResponse"]["statusCode"] == 403
        db.delete("grants", where=[("persona", "=", "alex"), ("component", "=", "knowledge-search")])
        assert intercept(request("tools/call", "knowledge___search"), db, "alex", "test-gateway")["mcp"]["transformedGatewayResponse"]["statusCode"] == 403


@pytest.mark.parametrize("change", ["unknown_user", "expired", "admin", "other_gateway", "unknown_method", "binding_drift"])
def test_gateway_rejects_stale_identity_or_capability_before_outbound_auth(store, change):
    with store.tx() as db:
        subject, gateway, method = "alex", "test-gateway", "tools/call"
        if change == "unknown_user":
            subject = "someone_else"
        elif change == "expired":
            db.update("principals", {"expires": 0}, where=[("id", "=", "alex")])
        elif change == "admin":
            db.update("principals", {"body": json.dumps({**PERSONAS["alex"], "role": "admin"})}, where=[("id", "=", "alex")])
        elif change == "other_gateway":
            gateway = "other-gateway"
        elif change == "unknown_method":
            method = "resources/read"
        else:
            row = db.select("components", where=[("id", "=", "knowledge-search")]).fetchone()
            item = json.loads(row["body"])
            item["binding"]["schema_digest"] = "invalid"
            db.update("components", {"body": json.dumps(item)}, where=[("id", "=", item["id"])])
        result = intercept(request(method, "knowledge___search"), db, subject, gateway)
        assert result["mcp"]["transformedGatewayResponse"]["statusCode"] == 403
