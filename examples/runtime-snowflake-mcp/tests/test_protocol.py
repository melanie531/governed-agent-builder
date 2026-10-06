import socket
import json
import secrets
import threading
import time
from unittest.mock import Mock

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import pytest
import uvicorn

from snowflake_mcp.database import Database, Settings
from snowflake_mcp.server import create_server


@pytest.mark.parametrize("requested_secondary_roles", ["", "NONE"])
def test_real_streamable_http_discovery_and_query_without_snowflake_at_startup(
    requested_secondary_roles,
):
    cursor = Mock(description=[("N",)], sfqid="protocol-query")
    cursor.fetchmany.return_value = [(1,)]
    cursor.fetchone.return_value = (
        "MCP_READER", json.dumps({"roles": "", "value": requested_secondary_roles}),
    )
    connection = Mock()
    connection.cursor.return_value = cursor
    connect = Mock(return_value=connection)
    database = Database(Settings("org-account", "MCP_READER", "READ_WH"),
                        access_token=secrets.token_urlsafe(32), connect=connect)
    app = create_server(database).streamable_http_app()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(500):
            if server.started:
                break
            time.sleep(.01)
        assert server.started
        url = f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"

        async def exercise():
            async with streamable_http_client(url) as (read, write, _):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    assert {t.name for t in tools.tools} == {
                        "test_connection", "list_databases", "list_schemas", "list_tables",
                        "list_views", "describe_table", "query"}
                    connect.assert_not_called()
                    bad = await client.call_tool("query", {"sql": "DROP TABLE t"})
                    assert bad.isError
                    connect.assert_not_called()
                    good = await client.call_tool("query", {"sql": "SELECT 1", "max_rows": 1})
                    assert not good.isError
                    result = good.structuredContent or json.loads(good.content[0].text)
                    assert result["rows"] == [[1]]
                    assert result["query_id"] == "protocol-query"
                    for name, args in (
                        ("list_schemas", {"database": "EXISTING_DB"}),
                        ("list_tables", {"database": "EXISTING_DB", "schema": "PUBLIC"}),
                        ("list_views", {"database": "EXISTING_DB", "schema": "PUBLIC"}),
                        ("describe_table", {"database": "EXISTING_DB", "schema": "PUBLIC", "table": "SALES"}),
                    ):
                        metadata = await client.call_tool(name, args)
                        assert not metadata.isError, name
        anyio.run(exercise)
        assert connection.close.call_count == 5
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
    assert not thread.is_alive()


def test_concurrent_http_users_use_separate_oauth_connections_and_anonymous_query_is_denied():
    from types import SimpleNamespace
    from cryptography.hazmat.primitives.asymmetric import rsa
    import httpx
    import jwt
    from snowflake_mcp.identity import OAuthSettings, UserAuthorization, USER_TOKEN_HEADER

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = OAuthSettings("us-east-1", "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_AbC",
                             "client123", "customer-users", "customer-provider", ("session:role:READER",))
    tokens = {name: jwt.encode({"iss": settings.issuer, "sub": name, "iat": int(time.time()),
              "exp": int(time.time()) + 300, "client_id": settings.client_id, "token_use": "access",
              "scope": "openid"}, key, algorithm="RS256") for name in ("alice", "bob")}
    access = {token: secrets.token_urlsafe(32) for token in tokens.values()}
    owners = {access[tokens[name]]: name for name in tokens}
    opened, closed = [], []

    def connect(**kwargs):
        assert kwargs["authenticator"] == "oauth"
        owner = owners[kwargs["token"]]
        opened.append(owner)
        cursor = Mock(description=[("CURRENT_USER()",)], sfqid="isolated-query-" + owner)
        cursor.fetchmany.return_value = [(owner,)]
        cursor.fetchone.return_value = ("READER", '{"roles":"","value":"NONE"}')
        return SimpleNamespace(cursor=lambda: cursor, close=lambda: closed.append(owner))

    native = SimpleNamespace(
        get_workload_access_token_for_jwt=lambda **kw: {"workloadAccessToken": kw["userToken"]},
        get_resource_oauth2_token=lambda **kw: {"accessToken": access[kw["workloadIdentityToken"]]})
    authorization = UserAuthorization(settings, client=native, signing_key=lambda _: key.public_key())
    def factory(headers):
        return Database(Settings("org-account", "READER", "READ_WH"),
                        access_token=authorization.token(headers), connect=connect)
    app = create_server(database_factory=factory).streamable_http_app()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(500):
            if server.started:
                break
            time.sleep(.01)
        assert server.started
        url = f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"

        async def exercise_user(name):
            headers = {USER_TOKEN_HEADER: tokens[name]} if name else {}
            async with httpx.AsyncClient(headers=headers) as transport:
                async with streamable_http_client(url, http_client=transport) as (read, write, _):
                    async with ClientSession(read, write) as client:
                        await client.initialize()
                        tools = await client.list_tools()
                        assert all("token" not in json.dumps(t.inputSchema).lower() for t in tools.tools)
                        result = await client.call_tool("query", {"sql": "SELECT CURRENT_USER()"})
                        assert all(token not in result.model_dump_json() for token in access.values())
                        if name:
                            assert not result.isError
                            content = result.structuredContent or json.loads(result.content[0].text)
                            assert content["rows"] == [[name]]
                        else:
                            assert result.isError
                            assert "Sign in to Studio" in result.content[0].text
        async def exercise():
            await exercise_user(None)
            assert not opened
            async with anyio.create_task_group() as tasks:
                for name in tokens:
                    tasks.start_soon(exercise_user, name)
        anyio.run(exercise)
        assert sorted(opened) == sorted(closed) == ["alice", "bob"]
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
    assert not thread.is_alive()


def test_gateway_tokens_are_isolated_over_real_http_without_identity_exchange(monkeypatch):
    """The IAM caller supplies a Snowflake token; the Runtime never acquires one."""
    from types import SimpleNamespace
    import httpx
    import snowflake.connector
    import boto3

    header = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Snowflake-Token"
    tokens = {secrets.token_urlsafe(32): name for name in ("alice", "bob")}
    opened, closed = [], []

    def connect(**kwargs):
        assert kwargs["authenticator"] == "oauth"
        owner = tokens[kwargs["token"]]
        opened.append(owner)
        cursor = Mock(description=[("CURRENT_USER()",)], sfqid="query-" + owner)
        cursor.fetchmany.return_value = [(owner,)]
        cursor.fetchone.return_value = ("READER", '{"roles":"","value":"NONE"}')
        return SimpleNamespace(cursor=lambda: cursor, close=lambda: closed.append(owner))

    for name, value in {
        "SNOWFLAKE_ACCOUNT": "org-account", "SNOWFLAKE_ROLE": "READER",
        "SNOWFLAKE_WAREHOUSE": "READ_WH", "SNOWFLAKE_AUTH_SOURCE": "gateway",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(snowflake.connector, "connect", connect)
    # An external SDK seam: token acquisition must not occur in this mode.
    monkeypatch.setattr(boto3, "client", Mock(side_effect=AssertionError("Unexpected Identity call")))
    app = create_server().streamable_http_app()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(500):
            if server.started:
                break
            time.sleep(.01)
        assert server.started
        url = f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"

        async def exercise_user(token):
            async with httpx.AsyncClient(headers={header: token} if token else {}) as transport:
                async with streamable_http_client(url, http_client=transport) as (read, write, _):
                    async with ClientSession(read, write) as client:
                        await client.initialize()
                        tools = await client.list_tools()
                        assert len(tools.tools) == 7
                        result = await client.call_tool("query", {"sql": "SELECT CURRENT_USER()"})
                        assert all(value not in result.model_dump_json() for value in tokens)
                        if token:
                            assert not result.isError
                            content = result.structuredContent or json.loads(result.content[0].text)
                            assert content["rows"] == [[tokens[token]]]
                        else:
                            assert result.isError
                            assert "Snowflake authorization is required" in result.content[0].text

        async def exercise():
            await exercise_user(None)
            assert not opened
            async with anyio.create_task_group() as tasks:
                for token in tokens:
                    tasks.start_soon(exercise_user, token)
        anyio.run(exercise)
        assert sorted(opened) == sorted(closed) == ["alice", "bob"]
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
    assert not thread.is_alive()


def test_complete_package_starts_without_provider_environment_and_denies_anonymous_tools(monkeypatch):
    import os
    import main
    import httpx
    from snowflake_mcp import token_validation
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.status = 200
    response.read.return_value = json.dumps({"data": [["TEST_USER", main.SNOWFLAKE_ROLE]]}).encode()
    provider = Mock(return_value=response)
    monkeypatch.setattr(token_validation, "urlopen", provider)
    for name in list(os.environ):
        if name.startswith(("SNOWFLAKE_", "OAUTH_", "STUDIO_TOKEN_")):
            monkeypatch.delenv(name)
    app = main.create_package_server().streamable_http_app()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(500):
            if server.started:
                break
            time.sleep(.01)
        assert server.started
        async def exercise():
            url = f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"
            # The shared authorizer uses a fresh, stateless MCP request. Exercise
            # exactly that boundary before any client initialization.
            async with httpx.AsyncClient() as transport:
                validation = await transport.post(url, json={
                    "jsonrpc": "2.0", "id": "studio-package-auth", "method": "tools/call",
                    "params": {"name": "_studio_validate_token", "arguments": {}},
                }, headers={"Accept": "application/json, text/event-stream",
                    "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Access-Token": secrets.token_urlsafe(32)})
                assert validation.status_code == 200
                assert validation.json()["result"]["structuredContent"] == {"authorized": True}
            async with streamable_http_client(url) as (read, write, _):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    assert len(tools.tools) == 8
                    assert "_studio_validate_token" in {t.name for t in tools.tools}
                    probe = await client.call_tool("_studio_validate_token", {})
                    assert probe.structuredContent == {"authorized": False}
                    denied = await client.call_tool("query", {"sql": "SELECT 1"})
                    assert denied.isError
        anyio.run(exercise)
        assert provider.call_count == 1
        assert provider.call_args.args[0].full_url == f"https://{main.SNOWFLAKE_ACCOUNT}.snowflakecomputing.com/api/v2/statements"
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
    assert not thread.is_alive()
