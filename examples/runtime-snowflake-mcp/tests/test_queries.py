import json
import secrets
from unittest.mock import Mock

import pytest

from snowflake_mcp.database import Database, Settings, readonly_sql


@pytest.mark.parametrize("sql", [
    "select current_version()", "with t as (select 1 as n) select n from t",
    "select count(*) from db.schema.orders", "select 1 union all select 2",
    "select sum(case when region = 'APAC' then revenue else 0 end) from d.s.sales",
    "select date_trunc('month', sale_date), sum(revenue) from d.s.sales group by 1",
])
def test_read_queries(sql):
    assert readonly_sql(sql)


@pytest.mark.parametrize("sql", [
    "", "select 1; drop table t", "delete from t", "create table t as select 1",
    "call proc()", "use role accountadmin", "select * into t from s",
    "select system$send_email('integration','recipient','subject','message')",
    "select db.schema.external_function(1)", "with t as (delete from a) select * from t",
    "copy into 's3://example/out' from t", "select * from table(result_scan('id'))",
])
def test_reject_non_read_queries(sql):
    with pytest.raises(ValueError):
        readonly_sql(sql)


def test_oauth_bound_role_deadline_truncation_and_closure():
    cursor = Mock()
    cursor.description = [("N",)]
    cursor.sfqid = "query-id"
    cursor.fetchmany.return_value = [(1,), (2,), (3,)]
    cursor.fetchone.return_value = ("MCP_READER", '{"roles":"","value":"NONE"}')
    connection = Mock()
    connection.cursor.return_value = cursor
    connect = Mock(return_value=connection)
    token = secrets.token_urlsafe(32)
    db = Database(Settings(account="org-account", role="MCP_READER", warehouse="READ_WH"),
                  access_token=token, connect=connect)
    result = db.query("select 1", max_rows=2)
    args = connect.call_args.kwargs
    assert args["authenticator"] == "oauth"
    assert args["token"] == token
    assert "workload_identity_provider" not in args
    assert args["role"] == "MCP_READER"
    assert args["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"] == 20
    assert "password" not in args and "user" not in args
    assert cursor.execute.call_args_list[0].args == ("SELECT CURRENT_ROLE(), CURRENT_SECONDARY_ROLES()",)
    assert all(call.args != ("USE SECONDARY ROLES NONE",) for call in cursor.execute.call_args_list)
    assert result["rows"] == [[1], [2]] and result["truncated"]
    assert result["query_id"] == "query-id"
    cursor.close.assert_called_once()
    connection.close.assert_called_once()
    assert token not in json.dumps(result)


def test_oauth_login_requests_no_secondary_roles():
    cursor = Mock()
    cursor.fetchone.return_value = ("MCP_READER", '{"roles":"","value":"NONE"}')
    cursor.description = [("N",)]
    cursor.sfqid = "query-id"
    cursor.fetchmany.return_value = [(1,)]
    connection = Mock(cursor=Mock(return_value=cursor))
    connect = Mock(return_value=connection)
    db = Database(Settings(account="org-account", role="MCP_READER", warehouse="READ_WH"),
                  access_token=secrets.token_urlsafe(32), connect=connect)
    assert db.test_connection()["rows"] == [[1]]
    assert connect.call_args.kwargs["secondary_roles"] == "NONE"
    assert all(call.args != ("USE SECONDARY ROLES NONE",) for call in cursor.execute.call_args_list)


def test_table_discovery_accepts_empty_requested_and_active_secondary_role_lists():
    cursor = Mock()
    cursor.fetchone.return_value = ("MCP_READER", '{"roles":"","value":""}')
    cursor.description = [("name",)]
    cursor.sfqid = "metadata-query-id"
    cursor.fetchmany.return_value = [("ORDERS",)]
    connection = Mock(cursor=Mock(return_value=cursor))
    connect = Mock(return_value=connection)
    db = Database(Settings(account="org-account", role="MCP_READER", warehouse="READ_WH"),
                  access_token=secrets.token_urlsafe(32), connect=connect)

    result = db.list_tables("DEMO_DB", "PUBLIC")

    assert result == {
        "columns": ["name"], "rows": [["ORDERS"]],
        "truncated": False, "query_id": "metadata-query-id",
    }
    assert connect.call_args.kwargs["secondary_roles"] == "NONE"
    assert [call.args[0] for call in cursor.execute.call_args_list] == [
        "SELECT CURRENT_ROLE(), CURRENT_SECONDARY_ROLES()",
        'SHOW TABLES IN SCHEMA "DEMO_DB"."PUBLIC" LIMIT 201',
    ]
    cursor.close.assert_called_once()
    connection.close.assert_called_once()


@pytest.mark.parametrize("secondary", [
    {"roles": "EXTRA_ROLE", "value": ""},
    {"roles": "EXTRA_ROLE", "value": "NONE"},
    {"roles": "", "value": "ALL"},
    {"roles": "", "value": "EXTRA_ROLE"},
    {"roles": "", "value": " "},
    {"roles": "", "value": "none"},
    {"roles": "", "value": None},
    {"roles": "", "value": []},
    {"roles": "", "value": False},
    {"roles": [], "value": ""},
    {"roles": None, "value": ""},
    {"roles": ""},
    {"value": ""},
    {},
    [],
    None,
])
def test_unrestricted_or_unknown_secondary_role_state_blocks_table_discovery(secondary):
    cursor = Mock()
    cursor.fetchone.return_value = ("MCP_READER", json.dumps(secondary))
    connection = Mock(cursor=Mock(return_value=cursor))
    db = Database(Settings(account="org-account", role="MCP_READER", warehouse="READ_WH"),
                  access_token=secrets.token_urlsafe(32), connect=Mock(return_value=connection))

    result = db.list_tables("DEMO_DB", "PUBLIC")

    assert result["failure_stage"] == "session_setup"
    assert result["failure_step"] in {"inspect_roles", "verify_roles"}
    assert "rows" not in result
    assert [call.args[0] for call in cursor.execute.call_args_list] == [
        "SELECT CURRENT_ROLE(), CURRENT_SECONDARY_ROLES()",
    ]
    cursor.close.assert_called_once()
    connection.close.assert_called_once()


def test_locked_snowflake_connector_sends_login_role_restriction(monkeypatch):
    from snowflake.connector.auth._auth import Auth
    from snowflake.connector.auth.oauth import AuthByOAuth

    class StopBeforeNetwork(Exception):
        pass

    rest = Mock()
    rest._connection._secondary_roles = "NONE"
    rest._post_request.side_effect = StopBeforeNetwork()
    monkeypatch.setattr(Auth, "base_auth_data", lambda *args, **kwargs: {"data": {}})
    with pytest.raises(StopBeforeNetwork):
        Auth(rest).authenticate(AuthByOAuth(secrets.token_urlsafe(32)),
                                account="org-account", user="user", role="MCP_READER")
    body = json.loads(rest._post_request.call_args.args[2])
    assert body["data"]["SECONDARY_ROLES"] == "NONE"


def test_safe_error_identifies_the_failed_stage_without_leaking_exception_text():
    connect = Mock(side_effect=Exception("customer SQL or secret here"))
    db = Database(Settings(account="org-account", role="MCP_READER", warehouse="READ_WH"),
                  access_token=secrets.token_urlsafe(32), connect=connect)
    with pytest.raises(ValueError):
        db.query("drop table t")
    connect.assert_not_called()
    result = db.query("select 1")
    assert result == {
        "error": "Snowflake operation failed; no result was returned.",
        "failure_stage": "connect",
        "failure_step": "connect",
    }
    assert "customer" not in json.dumps(result)


@pytest.mark.parametrize("failure_stage,failure_step,failed_operation", [
    ("session_setup", "inspect_roles", 1),
    ("statement", "statement", 2),
])
def test_query_failure_after_login_reports_the_step_not_a_false_disconnect(
    failure_stage, failure_step, failed_operation
):
    cursor = Mock()
    cursor.execute.side_effect = [None] * (failed_operation - 1) + [Exception("private SQL and credentials")]
    cursor.fetchone.return_value = ("MCP_READER", '{"roles":"","value":"NONE"}')
    connection = Mock()
    connection.cursor.return_value = cursor
    db = Database(Settings(account="org-account", role="MCP_READER", warehouse="READ_WH"),
                  access_token=secrets.token_urlsafe(32), connect=Mock(return_value=connection))
    result = db.query("select 1")
    assert result == {
        "error": "Snowflake operation failed; no result was returned.",
        "failure_stage": failure_stage,
        "failure_step": failure_step,
    }
    assert "private" not in json.dumps(result)
    cursor.close.assert_called_once()
    connection.close.assert_called_once()


def test_active_secondary_roles_after_login_fail_closed_before_query():
    cursor = Mock()
    cursor.fetchone.return_value = ("MCP_READER", '{"roles":"EXTRA_ROLE","value":"ALL"}')
    connection = Mock(cursor=Mock(return_value=cursor))
    db = Database(Settings(account="org-account", role="MCP_READER", warehouse="READ_WH"),
                  access_token=secrets.token_urlsafe(32), connect=Mock(return_value=connection))
    assert db.query("select 1")["failure_step"] == "verify_roles"
    assert [call.args[0] for call in cursor.execute.call_args_list] == [
        "SELECT CURRENT_ROLE(), CURRENT_SECONDARY_ROLES()",
    ]


@pytest.mark.parametrize("requested_secondary_roles", ["", "NONE"])
def test_role_mismatch_fails_closed_without_running_user_statement(requested_secondary_roles):
    cursor = Mock()
    cursor.fetchone.return_value = (
        "ACCOUNTADMIN", json.dumps({"roles": "", "value": requested_secondary_roles}),
    )
    connection = Mock(cursor=Mock(return_value=cursor))
    db = Database(Settings(account="org-account", role="MCP_READER", warehouse="READ_WH"),
                  access_token=secrets.token_urlsafe(32), connect=Mock(return_value=connection))
    result = db.query("select 1")
    assert result["failure_step"] == "inspect_roles"
    assert cursor.execute.call_count == 1
    assert "ACCOUNTADMIN" not in json.dumps(result)


def test_all_secondary_roles_requested_after_login_fail_closed_even_if_none_active():
    cursor = Mock()
    cursor.fetchone.return_value = ("MCP_READER", '{"roles":"","value":"ALL"}')
    connection = Mock(cursor=Mock(return_value=cursor))
    db = Database(Settings(account="org-account", role="MCP_READER", warehouse="READ_WH"),
                  access_token=secrets.token_urlsafe(32), connect=Mock(return_value=connection))
    assert db.query("select 1")["failure_step"] == "verify_roles"
    assert cursor.execute.call_count == 1


def test_role_inspection_failure_stops_before_user_statement():
    cursor = Mock()
    cursor.execute.side_effect = Exception("secret")
    connection = Mock(cursor=Mock(return_value=cursor))
    db = Database(Settings(account="org-account", role="MCP_READER", warehouse="READ_WH"),
                  access_token=secrets.token_urlsafe(32), connect=Mock(return_value=connection))
    result = db.query("select 1")
    assert result["failure_step"] == "inspect_roles"
    assert cursor.execute.call_count == 1
    assert "secret" not in json.dumps(result)


def test_unparseable_secondary_role_state_fails_closed():
    cursor = Mock()
    cursor.fetchone.return_value = ("MCP_READER", "private malformed state")
    connection = Mock(cursor=Mock(return_value=cursor))
    db = Database(Settings(account="org-account", role="MCP_READER", warehouse="READ_WH"),
                  access_token=secrets.token_urlsafe(32), connect=Mock(return_value=connection))
    result = db.query("select 1")
    assert result["failure_step"] == "inspect_roles"
    assert cursor.execute.call_count == 1
    assert all(call.args != ("select 1",) for call in cursor.execute.call_args_list)


def test_cursor_creation_failure_is_distinct_from_role_setup():
    connection = Mock()
    connection.cursor.side_effect = Exception("private session state")
    db = Database(Settings(account="org-account", role="MCP_READER", warehouse="READ_WH"),
                  access_token=secrets.token_urlsafe(32), connect=Mock(return_value=connection))
    assert db.test_connection() == {
        "error": "Snowflake operation failed; no result was returned.",
        "failure_stage": "session_setup",
        "failure_step": "cursor",
    }
    connection.close.assert_called_once()


def test_provider_diagnostic_only_includes_bounded_numeric_code_and_sqlstate():
    class PrivateError(Exception):
        errno = 390100
        sqlstate = "08001"

    connect = Mock(side_effect=PrivateError("token=secret; customer SQL"))
    db = Database(Settings(account="org-account", role="MCP_READER", warehouse="READ_WH"),
                  access_token=secrets.token_urlsafe(32), connect=connect)
    result = db.test_connection()
    assert result == {
        "error": "Snowflake operation failed; no result was returned.",
        "failure_stage": "connect",
        "failure_step": "connect",
        "snowflake_errno": 390100,
        "snowflake_sqlstate": "08001",
    }
    assert "secret" not in json.dumps(result)


def test_unbounded_provider_diagnostic_is_not_returned():
    class PrivateError(Exception):
        errno = "token=secret"
        sqlstate = "secret"

    db = Database(Settings(account="org-account", role="MCP_READER", warehouse="READ_WH"),
                  access_token=secrets.token_urlsafe(32), connect=Mock(side_effect=PrivateError("secret")))
    result = db.test_connection()
    assert set(result) == {"error", "failure_stage", "failure_step"}
    assert "secret" not in json.dumps(result)


def test_metadata_quotes_identifiers_and_caps_output_bytes():
    cursor = Mock(description=[("TEXT",)], sfqid="id")
    cursor.fetchmany.return_value = [("x" * 70000,)]
    cursor.fetchone.return_value = ("MCP_READER", '{"roles":"","value":"NONE"}')
    connection = Mock()
    connection.cursor.return_value = cursor
    db = Database(Settings(account="org-account", role="MCP_READER", warehouse="READ_WH"),
                  access_token=secrets.token_urlsafe(32), connect=Mock(return_value=connection))
    result = db.list_tables('a";drop table x;--', "schema")
    assert cursor.execute.call_args.args[0] == 'SHOW TABLES IN SCHEMA "a"";drop table x;--"."schema" LIMIT 201'
    assert result["truncated"]
    assert len(json.dumps(result).encode()) < 65536
    connection.close.assert_called_once()
