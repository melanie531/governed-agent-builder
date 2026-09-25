"""Contract tests for the ALPR read-only Snowflake reader.

OFFLINE / NOT LIVE: these tests run against tests.alpr_fixture.FakeSqlApiTransport,
an SQL-API-shaped fixture. No network, no Snowflake, no credentials.
They pin the reader's fail-closed contract: fixed SQL whitelist over the four
approved views, same-request session pinning (USE ROLE GAB_QUERY_READONLY +
USE SECONDARY ROLES NONE + CURRENT_ROLE()/CURRENT_SECONDARY_ROLES() verification),
bound case_id parameters, budgets, and zero gold/base-table access.
"""
import re

import pytest

from backend.alpr_reader import (
    APPROVED_VIEWS,
    AlprReaderDenied,
    AlprReadOnlyReader,
    READER_ROLE,
)
from tests.alpr_fixture import FakeSqlApiTransport, SECONDARY_ALL


def make_reader(**kwargs):
    transport = FakeSqlApiTransport()
    return AlprReadOnlyReader(transport, **kwargs), transport


def test_approved_views_are_exactly_the_four_secure_views():
    assert APPROVED_VIEWS == ("ACCOUNT_VEHICLE", "BILLING_NOTICE", "REMEDIATION", "POLICY")
    assert READER_ROLE == "GAB_QUERY_READONLY"


def test_fetch_returns_case_rows_with_synthetic_label_and_ids():
    reader, _ = make_reader()
    result = reader.fetch("ACCOUNT_VEHICLE", "CASE-001")
    assert result["view"] == "ACCOUNT_VEHICLE"
    assert result["case_id"] == "CASE-001"
    assert result["live"] is False
    assert result["synthetic"] is True
    assert {row["CASE_ID"] for row in result["rows"]} == {"CASE-001"}
    assert {row["ATTRIBUTION_SIDE"] for row in result["rows"]} == {"ORIGINAL", "CORRECTED"}
    assert result["ids"]["event_ids"] == ["EV-001"]
    assert result["ids"]["correction_evidence_ids"] == ["REVIEW-001"]


def test_policy_fetch_exposes_policy_version_and_approval_status():
    reader, _ = make_reader()
    result = reader.fetch("POLICY", "CASE-004")
    assert result["policy_version"] == "DEMO-1"
    assert result["rows"][0]["APPROVAL_STATUS"] == "DEMO_ASSUMPTION_NOT_CUSTOMER_APPROVED"


def test_session_pinning_happens_in_the_same_request_as_the_select():
    reader, transport = make_reader()
    reader.fetch("BILLING_NOTICE", "CASE-002")
    assert len(transport.submitted) == 1, "role pinning and SELECT must share one request/session"
    body = transport.submitted[0]
    stmts = [s.strip() for s in body["statement"].split(";") if s.strip()]
    assert stmts[0] == "USE ROLE GAB_QUERY_READONLY"
    assert stmts[1] == "USE SECONDARY ROLES NONE"
    assert "CURRENT_ROLE()" in stmts[2] and "CURRENT_SECONDARY_ROLES()" in stmts[2]
    assert stmts[3].startswith("SELECT * FROM GAB_DEMO_DB.ALPR_APPROVED.BILLING_NOTICE")
    assert body["parameters"]["MULTI_STATEMENT_COUNT"] == "4"
    assert body["role"] == "GAB_QUERY_READONLY"


def test_case_id_is_bound_not_interpolated():
    reader, transport = make_reader()
    reader.fetch("REMEDIATION", "CASE-003")
    body = transport.submitted[0]
    assert "CASE-003" not in body["statement"]
    assert body["bindings"]["1"] == {"type": "TEXT", "value": "CASE-003"}
    assert "WHERE CASE_ID = ?" in body["statement"]


def test_role_mismatch_is_rejected_before_reading_data():
    transport = FakeSqlApiTransport(force_role="GAB_BOOTSTRAP_ROLE")
    reader = AlprReadOnlyReader(transport)
    with pytest.raises(AlprReaderDenied, match="CURRENT_ROLE"):
        reader.fetch("ACCOUNT_VEHICLE", "CASE-001")
    # verify handle fetched, data handle NOT fetched: submit(1) + verify fetch(1) only
    assert transport.calls == 2


def test_active_secondary_roles_are_rejected():
    transport = FakeSqlApiTransport(force_secondary=SECONDARY_ALL)
    reader = AlprReadOnlyReader(transport)
    with pytest.raises(AlprReaderDenied, match="SECONDARY"):
        reader.fetch("ACCOUNT_VEHICLE", "CASE-001")


def test_unknown_view_and_arbitrary_sql_are_impossible():
    reader, transport = make_reader()
    for bad in ("EXPECTED_CASE_RESULTS", "CASES", "ACCOUNT_VEHICLE; DROP TABLE X",
                "ALPR_DEMO.CASES", "account_vehicle "):
        with pytest.raises(AlprReaderDenied, match="view"):
            reader.fetch(bad, "CASE-001")
    assert transport.submitted == [], "rejected views must never reach the transport"


def test_invalid_case_id_is_rejected():
    reader, transport = make_reader()
    for bad in ("CASE-1", "CASE-0001", "' OR 1=1", "", None, "CASE-001 OR TRUE"):
        with pytest.raises(AlprReaderDenied, match="case_id"):
            reader.fetch("POLICY", bad)
    assert transport.submitted == []


def test_no_statement_ever_references_gold_or_base_schema():
    reader, transport = make_reader()
    for view in APPROVED_VIEWS:
        reader.fetch(view, "CASE-005")
    joined = " ".join(b["statement"].upper() for b in transport.submitted)
    assert "EXPECTED_CASE_RESULTS" not in joined
    assert "ALPR_DEMO" not in joined  # base schema is off limits; only ALPR_APPROVED views
    assert joined.count("ALPR_APPROVED") == len(APPROVED_VIEWS)


def test_row_budget_is_enforced():
    reader = AlprReadOnlyReader(FakeSqlApiTransport(), max_rows=1)
    with pytest.raises(AlprReaderDenied, match="row budget"):
        reader.fetch("ACCOUNT_VEHICLE", "CASE-001")  # CASE-001 has 2 rows


def test_call_budget_is_enforced():
    reader, _ = make_reader(max_requests=2)
    reader.fetch("POLICY", "CASE-001")
    reader.fetch("POLICY", "CASE-002")
    with pytest.raises(AlprReaderDenied, match="request budget"):
        reader.fetch("POLICY", "CASE-003")


def test_time_budget_is_enforced():
    clock = iter([0.0, 100.0]).__next__
    reader = AlprReadOnlyReader(FakeSqlApiTransport(), max_seconds=5, clock=clock)
    with pytest.raises(AlprReaderDenied, match="time budget"):
        reader.fetch("POLICY", "CASE-001")


def test_reader_fails_closed_without_a_transport():
    with pytest.raises(AlprReaderDenied, match="transport"):
        AlprReadOnlyReader(None)


def test_select_template_is_the_frozen_whitelist_shape():
    reader, transport = make_reader()
    reader.fetch("ACCOUNT_VEHICLE", "CASE-006")
    select = [s.strip() for s in transport.submitted[0]["statement"].split(";") if s.strip()][3]
    assert re.fullmatch(
        r"SELECT \* FROM GAB_DEMO_DB\.ALPR_APPROVED\.ACCOUNT_VEHICLE WHERE CASE_ID = \? LIMIT \d+",
        select)
