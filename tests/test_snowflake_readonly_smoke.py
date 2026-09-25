"""Guardrails of the standalone Snowflake smoke, with mocked SSM and driver. Never connects."""
import json
from pathlib import Path

import pytest

from scripts import snowflake_readonly_smoke as smoke

SECRET = "SYNTHETIC-PRIVATE-KEY-DO-NOT-LOG"
ENV = {"SNOWFLAKE_SMOKE": "1", **{var: f"/synthetic/snowflake-smoke/{key}" for key, var in smoke.SSM_ENV.items()}}
VALUES = {"account": "SYN-ACCOUNT", "user": "SYN_SMOKE_USER", "private_key": SECRET, "role": "SYN_READONLY_ROLE",
          "warehouse": "SYN_WH", "database": "SYN_DB", "schema": "SYN_APPROVED", "approved_views": '["SYN_ACCOUNT_HEALTH_V"]'}


@pytest.fixture(autouse=True)
def no_aws(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("AWS client forbidden in offline tests")
    monkeypatch.setattr(smoke.boto3, "client", forbidden)


class FakeSSM:
    def __init__(self, values=VALUES):
        self.values, self.requests = values, []

    def get_parameter(self, Name, WithDecryption):
        self.requests.append((Name, WithDecryption))
        return {"Parameter": {"Value": self.values[Name.rsplit("/", 1)[1]]}}


class FakeConnection:
    def __init__(self):
        self.executed, self.closed = [], False
        self.description = [("ACCOUNT",), ("HEALTH",)]

    def cursor(self):
        return self

    def execute(self, sql):
        self.executed.append(sql)

    def fetchall(self):
        return [("SYN-ACCT-001", "amber")]

    def close(self):
        self.closed = True


def run(capsys, argv, env=ENV, ssm=None, connect=None):
    ssm = ssm or FakeSSM()
    connections = []

    def fake_connect(config):
        connections.append(FakeConnection())
        return connections[-1]
    code = smoke.main(argv, env=env, ssm=ssm, connect=connect or fake_connect)
    out = capsys.readouterr().out
    assert SECRET not in out and "SYN-ACCOUNT" not in out and "SYN_SMOKE_USER" not in out
    return code, json.loads(out), ssm, connections


@pytest.mark.parametrize("env", [{}, {**ENV, "SNOWFLAKE_SMOKE": "0"}, {k: v for k, v in ENV.items() if k != "SNOWFLAKE_SMOKE"}])
def test_refuses_without_explicit_opt_in(capsys, env):
    code, out, ssm, connections = run(capsys, ["--query", "SYN_ACCOUNT_HEALTH_V"], env=env)
    assert code == 2 and out["status"] == "REFUSED" and not ssm.requests and not connections


@pytest.mark.parametrize("var", list(smoke.SSM_ENV.values()))
def test_refuses_without_every_ssm_parameter_name(capsys, var):
    env = {k: v for k, v in ENV.items() if k != var}
    code, out, ssm, connections = run(capsys, ["--query", "SYN_ACCOUNT_HEALTH_V"], env=env)
    assert code == 2 and var in out["reason"] and not ssm.requests and not connections


def test_refuses_values_passed_instead_of_ssm_parameter_names(capsys):
    env = {**ENV, "SNOWFLAKE_SMOKE_SSM_PRIVATE_KEY": SECRET, "SNOWFLAKE_SMOKE_SSM_ACCOUNT": "syn-org-syn-account"}
    code, out, ssm, connections = run(capsys, ["--query", "SYN_ACCOUNT_HEALTH_V"], env=env)
    assert code == 2 and "not values" in out["reason"] and not ssm.requests and not connections


@pytest.mark.parametrize("query", [
    "SELECT * FROM SYN_DB.RAW.CUSTOMERS",
    "SYN_ACCOUNT_HEALTH_V; DROP TABLE SYN_ACCOUNT_HEALTH_V",
    'SYN_ACCOUNT_HEALTH_V" UNION SELECT 1 --',
    "syn_account_health_v",
])
def test_rejects_raw_sql_before_reading_ssm(capsys, query):
    code, out, ssm, connections = run(capsys, ["--query", query])
    assert code == 2 and "not SQL" in out["reason"] and not ssm.requests and not connections


def test_rejects_view_outside_ssm_whitelist_without_connecting(capsys):
    code, out, ssm, connections = run(capsys, ["--query", "SYN_RAW_CUSTOMERS"])
    assert code == 2 and "whitelisted" in out["reason"] and not connections
    assert all(decrypt for _, decrypt in ssm.requests)


@pytest.mark.parametrize("override", [
    {"approved_views": '"SYN_ACCOUNT_HEALTH_V"'},
    {"approved_views": "[]"},
    {"approved_views": '["SYN_V; DROP TABLE X"]'},
    {"approved_views": "not json"},
    {"schema": 'SYN_APPROVED"; DROP SCHEMA X; --'},
])
def test_rejects_malformed_whitelist_or_identifiers_from_ssm(capsys, override):
    code, out, _, connections = run(capsys, ["--query", "SYN_ACCOUNT_HEALTH_V"], ssm=FakeSSM({**VALUES, **override}))
    assert code == 2 and out["status"] == "REFUSED" and not connections


def test_whitelisted_view_runs_one_fixed_read_only_statement_and_reports_counts_only(capsys):
    code, out, ssm, connections = run(capsys, ["--query", "SYN_ACCOUNT_HEALTH_V"])
    assert code == 0 and out == {"status": "PASS", "row_count": 1, "column_count": 2, "row_limit": 5}
    assert connections[0].executed == ['SELECT * FROM "SYN_DB"."SYN_APPROVED"."SYN_ACCOUNT_HEALTH_V" LIMIT 5']
    assert connections[0].closed
    assert sorted(name for name, _ in ssm.requests) == sorted(ENV[var] for var in smoke.SSM_ENV.values())


def test_driver_failure_reports_type_only_never_the_message(capsys):
    def failing_connect(config):
        raise RuntimeError(f"auth failed for {config['user']}@{config['account']} key={config['private_key']}")
    code, out, _, _ = run(capsys, ["--query", "SYN_ACCOUNT_HEALTH_V"], connect=failing_connect)
    assert code == 1 and out == {"status": "FAILED", "error": "RuntimeError"}


def test_smoke_is_not_wired_into_the_app():
    root = Path(__file__).resolve().parents[1]
    for path in [*(root / "backend").rglob("*.py"), *(root / "runtime").rglob("*.py")]:
        assert "snowflake_readonly_smoke" not in path.read_text(), path
