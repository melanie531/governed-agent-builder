import json
import secrets
from types import SimpleNamespace

from botocore.credentials import Credentials
import httpx
import pytest

from foundation_harness.journey_mcp import GatewayMCP, GatewayFailure, USER_TOKEN_HEADER


def transport(monkeypatch, token):
    requests = []
    def handle(request):
        requests.append(request)
        body = json.loads(request.content)
        result = ({"capabilities": {"tools": {}}} if body["method"] == "initialize" else
                  {"tools": []} if body["method"] == "tools/list" else
                  {"content": [{"type": "text", "text": "query result"}]})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body.get("id"), "result": result})
    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    session = SimpleNamespace(region_name="us-east-1",
        get_credentials=lambda: Credentials(secrets.token_hex(10), secrets.token_hex(20)))
    gateway = GatewayMCP(session, "https://test.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
                         user_token=token, user_tools={"private___query"})
    return gateway, requests


def test_user_token_is_forwarded_only_to_selected_user_authorized_tools(monkeypatch):
    token = ".".join(secrets.token_urlsafe(12) for _ in range(3))
    gateway, requests = transport(monkeypatch, token)
    gateway.discover()
    gateway.call("shared___search", {"query": "ordinary request"})
    gateway.call("private___query", {"sql": "select current_user()"})
    assert all(USER_TOKEN_HEADER not in r.headers for r in requests[:-1])
    assert requests[-1].headers[USER_TOKEN_HEADER] == token
    assert USER_TOKEN_HEADER.lower() in requests[-1].headers["Authorization"]
    assert all(token.encode() not in r.content for r in requests)


def test_user_authorized_tool_requires_identity_before_gateway_dispatch(monkeypatch):
    gateway, requests = transport(monkeypatch, None)
    with pytest.raises(GatewayFailure, match="Sign in"):
        gateway.call("private___query", {"sql": "select 1"})
    assert not requests


def test_cognito_inbound_and_native_gateway_consent_stay_outside_tool_results(monkeypatch):
    from foundation_harness.journey_mcp import GatewayAuthorizationRequired
    from urllib.parse import urlencode
    token = ".".join(secrets.token_urlsafe(12) for _ in range(3))
    uri = "urn:ietf:params:oauth:request_uri:" + secrets.token_hex(16)
    url = "https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/authorize?" + urlencode({"request_uri": uri})
    requests = []
    def handle(request):
        requests.append(request)
        body = json.loads(request.content)
        if body["method"] == "tools/call":
            return httpx.Response(400, json={"jsonrpc": "2.0", "id": body["id"],
                "error": {"code": -32042, "message": "Authorization required",
                          "data": {"elicitations": [{"mode": "url", "url": url, "elicitationId": "consent"}]}}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body.get("id"),
            "result": {"capabilities": {"tools": {}}} if body["method"] == "initialize" else {"tools": []}})
    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(handle), **kw))
    session = SimpleNamespace(region_name="us-east-1",
        get_credentials=lambda: (_ for _ in ()).throw(AssertionError("Cognito Gateway must not use IAM signing")))
    gateway = GatewayMCP(session, "https://users.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
                         user_token=token, auth_type="COGNITO")
    gateway.discover()
    with pytest.raises(GatewayAuthorizationRequired) as caught:
        gateway.call("data___query", {"sql": "select 1"})
    assert caught.value.challenge == {"authorization_url": url, "session_uri": uri, "tool_name": "data___query"}
    assert uri not in str(caught.value) and token not in str(caught.value)
    assert all(r.headers["authorization"] == "Bearer " + token for r in requests)
    assert all(USER_TOKEN_HEADER not in r.headers for r in requests)
    assert all(r.headers["mcp-protocol-version"] == "2025-11-25" for r in requests)


def test_cognito_gateway_uses_negotiated_protocol_after_initialize(monkeypatch):
    token = ".".join(secrets.token_urlsafe(12) for _ in range(3))
    requests = []

    def handle(request):
        requests.append(request)
        body = json.loads(request.content)
        if body["method"] == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}}
        elif request.headers["mcp-protocol-version"] != "2025-03-26":
            return httpx.Response(400, json={"jsonrpc": "2.0", "id": body.get("id"),
                "error": {"code": -32600, "message": "Unsupported protocol version: 2025-11-25"}})
        elif body["method"] == "tools/list":
            result = {"tools": [{"name": "data___test_connection", "inputSchema": {"type": "object"}}]}
        else:
            result = {"content": [{"type": "text", "text": "connected"}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body.get("id"), "result": result})

    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(handle), **kw))
    session = SimpleNamespace(region_name="us-east-1",
        get_credentials=lambda: (_ for _ in ()).throw(AssertionError("Cognito Gateway must not use IAM signing")))
    gateway = GatewayMCP(session, "https://users.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
                         user_token=token, auth_type="COGNITO")

    gateway.discover()
    assert gateway.call("data___test_connection", {}) == "connected"
    assert requests[0].headers["mcp-protocol-version"] == "2025-11-25"
    assert all(r.headers["mcp-protocol-version"] == "2025-03-26" for r in requests[1:])


def test_gateway_forces_provider_consent_on_only_the_first_cognito_tool_call(monkeypatch):
    token = ".".join(secrets.token_urlsafe(12) for _ in range(3))
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        result = ({"capabilities": {"tools": {}}} if body["method"] == "initialize" else
                  {"tools": []} if body["method"] == "tools/list" else
                  {"content": [{"type": "text", "text": "ok"}]})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body.get("id"), "result": result})

    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(handle), **kw))
    session = SimpleNamespace(region_name="us-east-1",
        get_credentials=lambda: (_ for _ in ()).throw(AssertionError("Cognito Gateway must not use IAM signing")))
    gateway = GatewayMCP(session, "https://users.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
                         user_token=token, auth_type="COGNITO", force_authentication=True)
    gateway.discover()
    gateway.call("data___test_connection", {})
    gateway.call("data___list_tables", {})
    calls = [body for body in requests if body["method"] == "tools/call"]
    assert calls[0]["params"]["_meta"] == {
        "aws.bedrock-agentcore.gateway/credentialProviderConfiguration": {
            "oauthCredentialProvider": {"forceAuthentication": True}}}
    assert "_meta" not in calls[1]["params"]
    assert all("_meta" not in body["params"] for body in requests if body["method"] != "tools/call")


def test_iam_gateway_never_sends_oauth_force_metadata(monkeypatch):
    gateway, requests = transport(monkeypatch, None)
    gateway.call("shared___search", {"query": "safe"})
    assert "_meta" not in json.loads(requests[-1].content)["params"]
