"""ALPR specialists over the governed Snowflake views: network-free via the committed snapshot."""
import importlib.util
import json
from pathlib import Path

import pytest

from backend import alpr, harness

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("alpr_provision", ROOT / "scripts/alpr_snowflake_provision.py")
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)

REMEDIATION, CONNECTOR = "agent-alpr-remediation", "snowflake-alpr-views"
UNRESOLVED = ("MANUAL_REVIEW_MISSING_OWNERSHIP", "MANUAL_REVIEW_CONFLICTING_OWNERSHIP")


def manifest(*tools):
    return {"tools": list(tools), "component_versions": dict.fromkeys(tools, "1")}


def call(tool, question, scope=None):
    return harness.call_tool(manifest(tool), tool, {"question": question}, scope or harness.tool_scope(tool))


@pytest.mark.parametrize("case", provision.CASES, ids=[c[0] for c in provision.CASES])
def test_remediation_specialist_reaches_each_expected_outcome(case):
    case_id, *_, expected, wrong, correct, amount, _notes = case
    result = call(REMEDIATION, f"Investigate {case_id.lower()} for a misread plate")
    findings = result["findings"]
    assert findings["outcome"] == expected
    assert float(findings["financial_review_amount"]) == float(amount or 0)
    assert findings["correct_party_account"] == (None if expected in UNRESOLVED else correct)
    if wrong:
        assert wrong in findings["wrong_party_accounts"]
    assert result["read_only"] is True and result["actions_executed"] == []
    assert findings["policy_is_demo_assumption"] is True
    assert [c["source"] for c in result["inner_calls"]] == [f"snowflake:{q}" for q in alpr.VIEWS]


def test_view_rows_never_expose_expected_outcomes():
    snapshot = json.loads(alpr.SNAPSHOT.read_text())
    columns = {k for rows in snapshot["rows"].values() for row in rows for k in row}
    assert not {c for c in columns if c.startswith("EXPECTED_")}
    assert snapshot["provenance"]["role"] == alpr.ROLE and snapshot["provenance"]["secondary_roles"] == "NONE"


def test_harness_views_match_the_governed_views():
    assert {q: v["view"] for q, v in harness.SNOWFLAKE_VIEWS[CONNECTOR]["queries"].items()} == {q: v for q, (v, _) in alpr.VIEWS.items()}


def test_account_and_billing_specialists_read_only_their_own_view():
    owner = call("agent-alpr-account-vehicle", "Who owned the plate at passage ALPR-C006?")
    assert owner["findings"]["ownership_status"] == "CONFLICTING"
    assert owner["findings"]["event_time_owner_accounts"] == ["ACCT-1011", "ACCT-1012"]
    billing = call("agent-alpr-billing-notice", "Who was billed for ALPR-C001?")
    assert billing["findings"]["billing_status"] == "DOUBLE_BILLED"
    assert harness.tool_scope("agent-alpr-billing-notice")["data"] == ["GAB_DEMO_DB.ALPR_INVESTIGATION_APPROVED.VW_CHARGES_NOTICES_PAYMENTS"]


def test_case_id_is_required_and_validated():
    with pytest.raises(harness.ToolDenied, match="ALPR_CASE_ID_REQUIRED"):
        call(REMEDIATION, "Investigate the double billing")
    scope = harness.tool_scope(CONNECTOR)
    for bad in ("ALPR-C001' OR 1=1 --", "C001", 7):
        with pytest.raises(harness.ToolDenied, match="SNOWFLAKE_QUERY_NOT_WHITELISTED"):
            harness.call_tool(manifest(CONNECTOR), CONNECTOR, {"query_id": "ownership_at_event", "case_id": bad}, scope)
    with pytest.raises(harness.ToolDenied, match="AGENT_TOOL_ARGUMENTS_REJECTED"):
        harness.call_tool(manifest(REMEDIATION), REMEDIATION, {"question": "ALPR-C001", "role": "ACCOUNTADMIN"}, harness.tool_scope(REMEDIATION))


def test_caller_scope_confines_inner_view_reads():
    scope = harness.tool_scope(REMEDIATION)
    narrowed = dict(scope, data=[v for v in scope["data"] if not v.endswith("VW_POLICY_AT_EVENT")])
    with pytest.raises(harness.ToolDenied, match="CALLER_DATA_OUT_OF_SCOPE"):
        call(REMEDIATION, "ALPR-C001", narrowed)


def test_unknown_case_is_reported_not_invented():
    result = call(REMEDIATION, "Investigate ALPR-C999")
    assert result["findings"]["outcome"] == "CASE_NOT_FOUND" and result["findings"]["recommended_actions"] == []


def test_live_source_fails_closed_without_snapshot_fallback(monkeypatch):
    monkeypatch.setenv("ALPR_VIEW_SOURCE", "live")
    monkeypatch.delenv("ALPR_SNOWFLAKE_KEY_PATH", raising=False)
    monkeypatch.setattr(alpr, "_live", {})
    with pytest.raises(harness.ToolDenied, match="ALPR_LIVE_VIEW_FAILED"):
        call(REMEDIATION, "ALPR-C001")
    monkeypatch.setenv("ALPR_VIEW_SOURCE", "other")
    with pytest.raises(harness.ToolDenied, match="ALPR_VIEW_SOURCE_INVALID"):
        call(REMEDIATION, "ALPR-C001")


@pytest.mark.parametrize("role, secondary, isolated", [
    (alpr.ROLE, '{"roles":"","value":""}', True),
    (alpr.ROLE, '{"roles":"GAB_BOOTSTRAP_ROLE","value":""}', False),
    ("GAB_BOOTSTRAP_ROLE", '{"roles":"","value":""}', False),
])
def test_live_session_must_be_the_readonly_role_without_secondary_roles(role, secondary, isolated):
    class Connection:
        executed, closed = [], False

        def cursor(self):
            return self

        def execute(self, sql):
            self.executed.append(sql)

        def fetchone(self):
            return role, secondary

        def close(self):
            self.closed = True

    connection = Connection()
    if isolated:
        assert alpr.isolate(connection) is connection
    else:
        with pytest.raises(alpr.ViewUnavailable, match="ALPR_READONLY_SESSION_NOT_ISOLATED"):
            alpr.isolate(connection)
    assert connection.executed[0] == "USE SECONDARY ROLES NONE" and connection.closed is not isolated


def test_fixture_runner_routes_alpr_questions():
    definition = dict(manifest(REMEDIATION), prompt="Cite sources.", skills=[], output_format="text")
    result = harness.run_case(definition, "ALPR investigation for ALPR-C007", {REMEDIATION: harness.tool_scope(REMEDIATION)})
    assert "WRONG_PARTY_REFUND_AND_REBILL_REVIEW" in result["output"]
    assert result["sources"] == ["agent:alpr-remediation@1"]
