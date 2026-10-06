import io
import json
import secrets
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import facade


@pytest.fixture
def boundary(monkeypatch):
    origin, token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    for key, value in {
        "ORIGIN_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:example/origin",
        "SNOWFLAKE_ACCOUNT": "org-account", "SNOWFLAKE_ROLE": "READER",
        "SNOWFLAKE_WAREHOUSE": "READ_WH",
        "RUNTIME_ARN": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example-123",
    }.items():
        monkeypatch.setenv(key, value)
    sdk = Mock()
    sdk.get_secret_value.return_value = {"SecretString": origin}
    sdk.invoke_agent_runtime.return_value = {
        "contentType": "application/json", "statusCode": 200,
        "response": io.BytesIO(b'{"jsonrpc":"2.0","id":1,"result":{}}'),
    }
    monkeypatch.setattr(facade.boto3, "client", Mock(return_value=sdk))
    facade.origin_secret.cache_clear()
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.status = 200
    response.read.return_value = json.dumps({"data": [["DEMO_USER", "READER"]]}).encode()
    http = Mock(return_value=response)
    monkeypatch.setattr(facade, "urlopen", http)
    return SimpleNamespace(origin=origin, token=token, sdk=sdk, http=http, response=response)


def event(boundary):
    return {
        "version": "2.0", "rawPath": "/mcp",
        "headers": {"authorization": "Bearer " + boundary.token, "x-studio-origin": boundary.origin},
        "requestContext": {"http": {"method": "POST"}},
        "body": '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}',
    }


def test_authorizer_checks_origin_then_snowflake_identity(boundary):
    request = event(boundary)
    request["headers"]["x-studio-origin"] = "invalid"
    assert facade.authorize(request, None) == {"isAuthorized": False}
    boundary.http.assert_not_called()
    request["headers"]["x-studio-origin"] = boundary.origin
    result = facade.authorize(request, None)
    assert result["isAuthorized"]
    assert boundary.token not in json.dumps(result)
    sent = boundary.http.call_args.args[0]
    assert sent.full_url == "https://org-account.snowflakecomputing.com/api/v2/statements"
    payload = json.loads(sent.data)
    assert payload["statement"] == "SELECT CURRENT_USER(), CURRENT_ROLE()"
    assert payload["role"] == "READER"
    boundary.response.read.return_value = b'{"data":[["DEMO_USER","ACCOUNTADMIN"]]}'
    assert facade.authorize(request, None) == {"isAuthorized": False}


def test_bridge_requires_authorizer_and_forwards_token_only_as_private_header(boundary):
    request = event(boundary)
    assert facade.invoke(request, None)["statusCode"] == 403
    boundary.sdk.invoke_agent_runtime.assert_not_called()
    request["requestContext"]["authorizer"] = {"lambda": {"snowflake": "verified"}}
    result = facade.invoke(request, None)
    assert result["statusCode"] == 200
    args = boundary.sdk.invoke_agent_runtime.call_args.kwargs
    assert json.loads(args["payload"])["method"] == "tools/list"
    assert boundary.token not in json.dumps({**args, "payload": args["payload"].decode()})
    hook = boundary.sdk.meta.events.register.call_args.args[1]
    outgoing = SimpleNamespace(headers={})
    hook(request=outgoing)
    assert outgoing.headers[facade.TOKEN_HEADER] == boundary.token
    assert boundary.origin not in outgoing.headers.values()


def test_static_facade_preserves_the_separate_authorizer_and_bridge_environments(boundary, monkeypatch):
    monkeypatch.delenv("RUNTIME_ARN")
    request = event(boundary)
    auth = facade.authorize(request, None)
    assert auth["isAuthorized"]
    monkeypatch.setenv("RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example-123")
    for key in ("ORIGIN_SECRET_ARN", "SNOWFLAKE_ACCOUNT", "SNOWFLAKE_ROLE", "SNOWFLAKE_WAREHOUSE"):
        monkeypatch.delenv(key)
    request["requestContext"]["authorizer"] = {"lambda": auth["context"]}
    assert facade.invoke(request, None)["statusCode"] == 200


def test_runtime_affinity_reuses_one_users_session_but_never_another_users(boundary):
    def call(token):
        request = event(boundary)
        request["headers"]["authorization"] = "Bearer " + token
        request["requestContext"]["authorizer"] = {"lambda": {"snowflake": "verified"}}
        boundary.sdk.invoke_agent_runtime.return_value["response"] = io.BytesIO(b'{"result":{}}')
        assert facade.invoke(request, None)["statusCode"] == 200
        return boundary.sdk.invoke_agent_runtime.call_args.kwargs["runtimeSessionId"]
    first = call(boundary.token)
    assert call(boundary.token) == first
    assert call(secrets.token_urlsafe(32)) != first
    assert boundary.token not in first


def python_binding(boundary, monkeypatch):
    sid = "a" * 32
    prefix = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/studio_python_"
    state = {"id": sid, "phase": "READY", "runtime_arn": prefix + sid[:24] + "-abc123",
        "runtime_id": "studio_python_" + sid[:24] + "-abc123", "runtime_version": "1",
        "snowflake_account": "org-account", "snowflake_role": "READER", "warehouse": "READ_WH"}
    monkeypatch.setenv("STATE_TABLE", "studio-state")
    monkeypatch.setenv("RUNTIME_ARN_PREFIX", prefix)
    def save():
        boundary.sdk.get_item.return_value = {"Item": {"body": {"S": json.dumps({
            "key": "mcp-python:" + sid, "body": json.dumps(state)})}}}
    save()
    boundary.sdk.get_agent_runtime_endpoint.return_value = {
        "agentRuntimeArn": state["runtime_arn"], "liveVersion": "1", "status": "READY"}
    request = event(boundary)
    request["rawPath"] = "/mcp/" + sid
    return state, request, save


def test_uploaded_python_facade_uses_owned_ready_binding_and_checks_live_runtime_version(boundary, monkeypatch):
    state, request, _ = python_binding(boundary, monkeypatch)
    auth = facade.authorize(request, None)
    assert auth["isAuthorized"]
    assert auth["context"]["python_id"] == state["id"]
    request["requestContext"]["authorizer"] = {"lambda": auth["context"]}
    assert facade.invoke(request, None)["statusCode"] == 200
    assert boundary.sdk.invoke_agent_runtime.call_args.kwargs["agentRuntimeArn"] == state["runtime_arn"]
    assert boundary.sdk.get_item.call_args.kwargs == {
        "TableName": "studio-state", "ConsistentRead": True,
        "Key": {"pk": {"S": "settings"}, "sk": {"S": json.dumps(["mcp-python:" + state["id"]], separators=(",", ":"))}}}
    boundary.sdk.invoke_agent_runtime.reset_mock()
    boundary.sdk.get_agent_runtime_endpoint.return_value["liveVersion"] = "2"
    assert facade.invoke(request, None)["statusCode"] == 502
    boundary.sdk.invoke_agent_runtime.assert_not_called()


@pytest.mark.parametrize("change", ["foreign_runtime", "not_ready", "missing", "bad_path", "different_binding"])
def test_uploaded_python_facade_fails_closed_before_token_or_runtime_dispatch(boundary, monkeypatch, change):
    state, request, save = python_binding(boundary, monkeypatch)
    auth = facade.authorize(request, None)
    request["requestContext"]["authorizer"] = {"lambda": auth["context"]}
    boundary.http.reset_mock()
    if change == "foreign_runtime":
        state["runtime_arn"] = state["runtime_arn"].replace("123456789012", "999999999999")
        save()
    elif change == "not_ready":
        state["phase"] = "DEPLOYING"
        save()
    elif change == "missing":
        boundary.sdk.get_item.return_value = {}
    elif change == "bad_path":
        request["rawPath"] = "/mcp/../another"
    else:
        state["warehouse"] = "DIFFERENT_WH"
        save()
    assert facade.invoke(request, None)["statusCode"] in (403, 404)
    boundary.sdk.invoke_agent_runtime.assert_not_called()
    if change != "different_binding":
        assert facade.authorize(request, None) == {"isAuthorized": False}
        boundary.http.assert_not_called()


@pytest.mark.parametrize("change", ["oversize", "batch", "bad_json", "get", "wrong_path"])
def test_bridge_rejects_unsupported_requests_without_runtime_call(boundary, change):
    request = event(boundary)
    request["requestContext"]["authorizer"] = {"lambda": {"snowflake": "verified"}}
    if change == "oversize":
        request["body"] = "a" * (128 * 1024 + 1)
    elif change == "batch":
        request["body"] = "[]"
    elif change == "bad_json":
        request["body"] = "invalid"
    elif change == "get":
        request["requestContext"]["http"]["method"] = "GET"
    else:
        request["rawPath"] = "/elsewhere"
    assert facade.invoke(request, None)["statusCode"] in (400, 404, 405, 413)
    boundary.sdk.invoke_agent_runtime.assert_not_called()


def test_failures_do_not_expose_remote_response_or_credentials(boundary):
    boundary.http.side_effect = ValueError(boundary.token)
    assert facade.authorize(event(boundary), None) == {"isAuthorized": False}
    request = event(boundary)
    request["requestContext"]["authorizer"] = {"lambda": {"snowflake": "verified"}}
    boundary.sdk.invoke_agent_runtime.side_effect = ValueError(boundary.token)
    result = facade.invoke(request, None)
    assert result["statusCode"] == 502
    assert boundary.token not in json.dumps(result)


def test_generic_package_authorization_delegates_to_its_own_validator(boundary, monkeypatch):
    state, request, save = python_binding(boundary, monkeypatch)
    for field in ("snowflake_account", "snowflake_role", "warehouse"):
        del state[field]
    state.update(connection_mode="PACKAGE", bearer_validation_tool="validate_access")
    save()
    boundary.sdk.invoke_agent_runtime.return_value["response"] = io.BytesIO(json.dumps({
        "jsonrpc": "2.0", "id": "studio-package-auth",
        "result": {"structuredContent": {"authorized": True}, "isError": False},
    }).encode())
    auth = facade.authorize(request, None)
    assert auth["isAuthorized"]
    assert auth["context"]["package"] == "verified"
    boundary.http.assert_not_called()  # Shared code makes no provider-specific query.
    sent = boundary.sdk.invoke_agent_runtime.call_args.kwargs
    assert json.loads(sent["payload"])["params"] == {"name": "validate_access", "arguments": {}}
    hook = boundary.sdk.meta.events.register.call_args.args[1]
    outgoing = SimpleNamespace(headers={})
    hook(request=outgoing)
    assert outgoing.headers == {
        "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Access-Token": boundary.token}
    assert boundary.token not in json.dumps(auth)
    boundary.sdk.invoke_agent_runtime.return_value["response"] = io.BytesIO(b'{"result":{}}')
    request["requestContext"]["authorizer"] = {"lambda": auth["context"]}
    assert facade.invoke(request, None)["statusCode"] == 200


@pytest.mark.parametrize("result", [
    {"structuredContent": {"authorized": False}},
    {"structuredContent": {"authorized": "true"}},
    {"structuredContent": {"authorized": True}, "isError": True},
    {"content": [{"type": "text", "text": '{"authorized":true}'}]},
    {},
])
def test_generic_package_rejects_missing_or_invalid_bearer_validation(boundary, monkeypatch, result):
    state, request, save = python_binding(boundary, monkeypatch)
    state.update(connection_mode="PACKAGE", bearer_validation_tool="validate_access")
    save()
    boundary.sdk.invoke_agent_runtime.return_value["response"] = io.BytesIO(json.dumps({
        "jsonrpc": "2.0", "id": "studio-package-auth", "result": result,
    }).encode())
    assert facade.authorize(request, None) == {"isAuthorized": False}
    boundary.http.assert_not_called()
