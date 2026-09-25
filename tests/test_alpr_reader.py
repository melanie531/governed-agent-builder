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
    assert stmts[0] == "USE SECONDARY ROLES NONE"
    assert stmts[1] == "USE ROLE GAB_QUERY_READONLY"
    assert "CURRENT_ROLE()" in stmts[2] and "CURRENT_SECONDARY_ROLES()" in stmts[2]
    assert stmts[3].startswith("SELECT * FROM GAB_DEMO_DB.ALPR_APPROVED.BILLING_NOTICE")
    assert body["parameters"]["MULTI_STATEMENT_COUNT"] == "4"
    assert body["role"] == "GAB_QUERY_READONLY"


def test_case_id_is_validated_safe_literal_and_bindings_are_absent():
    """Official SQL API docs: multi-statement requests do NOT support bindings.

    The case key is therefore embedded as a literal, but ONLY after strict
    ASCII CASE-[0-9]{3} validation: no arbitrary SQL can ever reach the text.
    """
    reader, transport = make_reader()
    reader.fetch("REMEDIATION", "CASE-003")
    body = transport.submitted[0]
    assert "bindings" not in body, "bindings are unsupported in multi-statement requests"
    assert "WHERE CASE_ID = 'CASE-003'" in body["statement"]


def test_case_id_validation_is_strict_ascii():
    reader, transport = make_reader()
    for bad in ("CASE-\uff10\uff10\uff11",  # fullwidth digits: \d matches, [0-9] must not
                "CASE-٠٠١",                # arabic-indic digits
                "case-001", "CASE-001\n", " CASE-001"):
        with pytest.raises(AlprReaderDenied, match="case_id"):
            reader.fetch("POLICY", bad)
    assert transport.submitted == []


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


def test_secondary_verification_rejects_missing_keys_or_unknown_structure():
    for weird in ('{"unexpected": ""}', '{"roles": ""}', '{"value": ""}',
                  '{}', 'not-json', '["roles"]', '{"roles":"","value":"","x":1}'):
        transport = FakeSqlApiTransport(force_secondary=weird)
        reader = AlprReadOnlyReader(transport)
        with pytest.raises(AlprReaderDenied, match="SECONDARY"):
            reader.fetch("ACCOUNT_VEHICLE", "CASE-001")


def test_secondary_verification_accepts_the_real_empty_shape():
    # The provisioned account really returns {"roles":"","value":""}.
    transport = FakeSqlApiTransport(force_secondary='{"roles":"","value":""}')
    reader = AlprReadOnlyReader(transport)
    assert reader.fetch("ACCOUNT_VEHICLE", "CASE-001")["row_count"] == 2


def test_multi_partition_results_fail_loud_never_drop_rows():
    transport = FakeSqlApiTransport(force_partitions=2)
    reader = AlprReadOnlyReader(transport)
    with pytest.raises(AlprReaderDenied, match="partition"):
        reader.fetch("ACCOUNT_VEHICLE", "CASE-001")


def test_live_flag_follows_the_real_transport():
    class LiveMarked(FakeSqlApiTransport):
        is_live = True

    assert AlprReadOnlyReader(FakeSqlApiTransport()).fetch("POLICY", "CASE-001")["live"] is False
    assert AlprReadOnlyReader(LiveMarked()).fetch("POLICY", "CASE-001")["live"] is True


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


def test_budget_counts_every_actual_http_call():
    # One fetch = 1 submit + 2 result GETs = 3 HTTP calls against the budget.
    reader, _ = make_reader(max_requests=6)
    reader.fetch("POLICY", "CASE-001")
    reader.fetch("POLICY", "CASE-002")
    with pytest.raises(AlprReaderDenied, match="request budget"):
        reader.fetch("POLICY", "CASE-003")


def test_budget_denies_mid_fetch_when_http_calls_exhaust_it():
    reader, _ = make_reader(max_requests=2)  # submit+verify ok, data GET must be denied
    with pytest.raises(AlprReaderDenied, match="request budget"):
        reader.fetch("POLICY", "CASE-001")


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
        r"SELECT \* FROM GAB_DEMO_DB\.ALPR_APPROVED\.ACCOUNT_VEHICLE "
        r"WHERE CASE_ID = 'CASE-[0-9]{3}' LIMIT [0-9]+",
        select)
