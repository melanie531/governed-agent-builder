"""Unified live-connector increments: protected key injection, real Snowflake query ids in
tool evidence, bounded queries, reconnect handling, and fail-closed behavior. OFFLINE:
nothing here logs into Snowflake; the connector module is faked.
"""
import json
import sys
import types

import pytest

from backend import alpr, harness


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    alpr._live.clear()
    alpr.consume_query_ids()
    for name in ("ALPR_VIEW_SOURCE", "ALPR_SNOWFLAKE_ACCOUNT", "ALPR_SNOWFLAKE_USER",
                 "ALPR_SNOWFLAKE_KEY_PATH", "ALPR_SNOWFLAKE_KEY_SSM_PARAM"):
        monkeypatch.delenv(name, raising=False)
    yield
    alpr._live.clear()
    alpr.consume_query_ids()


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, params=None):
        self.connection.statements.append((statement, params))
        self.sfqid = f"01a{len(self.connection.statements):04d}-fake"
        return self

    def fetchone(self):
        return (alpr.ROLE, json.dumps({"roles": []}))

    def fetchall(self):
        return [("ALPR-C001", "X")]

    @property
    def description(self):
        return [("CASE_ID",), ("VALUE",)]


class FakeConnection:
    def __init__(self):
        self.statements = []
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True


def install_fake_connector(monkeypatch, made):
    module = types.ModuleType("snowflake.connector")

    def connect(**kwargs):
        made.append(kwargs)
        return FakeConnection()

    module.connect = connect
    parent = types.ModuleType("snowflake")
    parent.connector = module
    monkeypatch.setitem(sys.modules, "snowflake", parent)
    monkeypatch.setitem(sys.modules, "snowflake.connector", module)


def live_env(monkeypatch, tmp_path):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    path = tmp_path / "k.p8"
    path.write_bytes(pem)
    monkeypatch.setenv("ALPR_VIEW_SOURCE", "live")
    monkeypatch.setenv("ALPR_SNOWFLAKE_ACCOUNT", "FAKE-ACCT")
    monkeypatch.setenv("ALPR_SNOWFLAKE_USER", "FAKE_USER")
    monkeypatch.setenv("ALPR_SNOWFLAKE_KEY_PATH", str(path))
    return pem


# ------------------------------------------------- query ids into evidence
def test_live_rows_record_real_query_id(monkeypatch, tmp_path):
    made = []
    install_fake_connector(monkeypatch, made)
    live_env(monkeypatch, tmp_path)
    rows = alpr.rows("ownership_at_event", "ALPR-C001")
    assert rows == [{"CASE_ID": "ALPR-C001", "VALUE": "X"}]
    ids = alpr.consume_query_ids()
    assert len(ids) == 1
    assert ids[0]["query_id"] == "ownership_at_event" and ids[0]["case_id"] == "ALPR-C001"
    assert ids[0]["snowflake_query_id"].endswith("-fake")
    assert alpr.consume_query_ids() == []  # consumed once


def test_connector_result_carries_snowflake_query_ids(monkeypatch, tmp_path):
    made = []
    install_fake_connector(monkeypatch, made)
    live_env(monkeypatch, tmp_path)
    manifest = {"tools": ["snowflake-alpr-views"], "component_versions": {"snowflake-alpr-views": "1"}}
    result = harness.call_tool(manifest, "snowflake-alpr-views",
                               {"query_id": "ownership_at_event", "case_id": "ALPR-C001"},
                               harness.tool_scope("snowflake-alpr-views"))
    assert result["snowflake_query_ids"], "live result must carry the real Snowflake query id"
    assert result["snowflake_query_ids"][0]["snowflake_query_id"].endswith("-fake")


def test_snapshot_results_have_no_query_ids(monkeypatch):
    manifest = {"tools": ["snowflake-alpr-views"], "component_versions": {"snowflake-alpr-views": "1"}}
    result = harness.call_tool(manifest, "snowflake-alpr-views",
                               {"query_id": "ownership_at_event", "case_id": "ALPR-C001"},
                               harness.tool_scope("snowflake-alpr-views"))
    assert result.get("snowflake_query_ids") == []


def test_specialist_inner_calls_carry_query_ids(monkeypatch, tmp_path):
    made = []
    install_fake_connector(monkeypatch, made)
    live_env(monkeypatch, tmp_path)
    tool = "agent-alpr-account-vehicle"
    manifest = {"tools": [tool], "component_versions": {tool: "1"}}
    monkeypatch.setattr(alpr, "review", lambda operation, case_id, found: {
        "case_id": case_id, "steps": [], "answer": "stub"})
    result = harness.call_tool(manifest, tool, {"question": "Who owned the plate in ALPR-C001?"},
                               harness.tool_scope(tool))
    inner = result["inner_calls"][0]
    assert inner["snowflake_query_ids"][0]["snowflake_query_id"].endswith("-fake")


# ------------------------------------------------- bounded + isolated connection
def test_connection_is_bounded_and_isolated(monkeypatch, tmp_path):
    made = []
    install_fake_connector(monkeypatch, made)
    live_env(monkeypatch, tmp_path)
    alpr.rows("ownership_at_event", "ALPR-C001")
    kwargs = made[0]
    assert kwargs["role"] == alpr.ROLE and kwargs["warehouse"] == alpr.WAREHOUSE
    assert kwargs["login_timeout"] <= 20 and kwargs["network_timeout"] <= 60
    assert int(kwargs["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"]) <= 60
    connection = alpr._live["connection"]
    assert ("USE SECONDARY ROLES NONE", None) in connection.statements
    select = [s for s, _ in connection.statements if s.startswith("SELECT * FROM")][0]
    assert f"LIMIT {alpr.ROW_LIMIT}" in select


def test_closed_connection_is_reopened(monkeypatch, tmp_path):
    made = []
    install_fake_connector(monkeypatch, made)
    live_env(monkeypatch, tmp_path)
    alpr.rows("ownership_at_event", "ALPR-C001")
    alpr._live["connection"].close()
    alpr.rows("ownership_at_event", "ALPR-C002")
    assert len(made) == 2


# ------------------------------------------------- protected key injection
def test_ssm_key_parameter_is_used_without_touching_disk(monkeypatch, tmp_path):
    made = []
    install_fake_connector(monkeypatch, made)
    pem = live_env(monkeypatch, tmp_path)
    monkeypatch.delenv("ALPR_SNOWFLAKE_KEY_PATH")
    monkeypatch.setenv("ALPR_SNOWFLAKE_KEY_SSM_PARAM", "/governed-agent-builder/alpr/key")
    calls = []

    class FakeSSM:
        def get_parameter(self, Name, WithDecryption):
            calls.append((Name, WithDecryption))
            return {"Parameter": {"Value": pem.decode()}}

    monkeypatch.setattr(alpr, "_ssm_client", lambda: FakeSSM())
    alpr.rows("ownership_at_event", "ALPR-C001")
    assert calls == [("/governed-agent-builder/alpr/key", True)]
    assert made[0]["private_key"]  # DER bytes passed in memory only


def test_missing_ssm_parameter_fails_closed(monkeypatch, tmp_path):
    made = []
    install_fake_connector(monkeypatch, made)
    live_env(monkeypatch, tmp_path)
    monkeypatch.delenv("ALPR_SNOWFLAKE_KEY_PATH")
    monkeypatch.setenv("ALPR_SNOWFLAKE_KEY_SSM_PARAM", "/missing")

    class FakeSSM:
        def get_parameter(self, Name, WithDecryption):
            raise RuntimeError("ParameterNotFound")

    monkeypatch.setattr(alpr, "_ssm_client", lambda: FakeSSM())
    with pytest.raises(alpr.ViewUnavailable, match="ALPR_LIVE_VIEW_FAILED"):
        alpr.rows("ownership_at_event", "ALPR-C001")


def test_live_without_credentials_fails_closed_no_snapshot(monkeypatch):
    monkeypatch.setenv("ALPR_VIEW_SOURCE", "live")
    with pytest.raises(alpr.ViewUnavailable):
        alpr.rows("ownership_at_event", "ALPR-C001")


def test_wrong_role_session_is_rejected(monkeypatch, tmp_path):
    made = []
    install_fake_connector(monkeypatch, made)
    live_env(monkeypatch, tmp_path)

    def bad_fetchone(self):
        return ("GAB_BOOTSTRAP_ROLE", json.dumps({"roles": []}))

    monkeypatch.setattr(FakeCursor, "fetchone", bad_fetchone)
    with pytest.raises(alpr.ViewUnavailable, match="NOT_ISOLATED"):
        alpr.rows("ownership_at_event", "ALPR-C001")
