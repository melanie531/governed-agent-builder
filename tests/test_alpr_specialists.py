"""Contract tests for the three ALPR investigation specialists.

OFFLINE / NOT LIVE: specialists run against the fail-closed reader wired to
tests.alpr_fixture.FakeSqlApiTransport. No network, no Snowflake.
Pins: specialists only issue whitelisted reads, never write, never see gold,
return evidence + recommendations only (advisory; nothing is executed).
"""
from decimal import Decimal

import pytest

from backend.alpr_reader import AlprReadOnlyReader, AlprReaderDenied
from backend.alpr_specialists import (
    AccountVehicleSpecialist,
    BillingNoticeSpecialist,
    RemediationSpecialist,
    investigate_case,
)
from tests.alpr_fixture import FakeSqlApiTransport


@pytest.fixture
def transport():
    return FakeSqlApiTransport()


@pytest.fixture
def reader(transport):
    return AlprReadOnlyReader(transport)


# ------------------------------------------------------------ Account/Vehicle
def test_account_vehicle_finds_single_corrected_owner(reader):
    finding = AccountVehicleSpecialist(reader).investigate("CASE-001")
    assert finding["correct_owner_count"] == 1
    assert finding["correct_owner_accounts"] == ["ACCT-C1"]
    assert finding["attribution"] == "SINGLE_CORRECTED_OWNER"
    assert finding["evidence"]["ownership_ids"] == ["OWN-C1", "OWN-W1"]
    assert finding["synthetic"] is True


def test_account_vehicle_reports_missing_event_time_owner(reader):
    finding = AccountVehicleSpecialist(reader).investigate("CASE-004")
    assert finding["correct_owner_count"] == 0
    assert finding["attribution"] == "MISSING_EVENT_TIME_OWNER"


def test_account_vehicle_reports_conflicting_owners(reader):
    finding = AccountVehicleSpecialist(reader).investigate("CASE-005")
    assert finding["correct_owner_count"] == 2
    assert finding["attribution"] == "CONFLICTING_EVENT_TIME_OWNERS"
    assert sorted(finding["correct_owner_accounts"]) == ["ACCT-C5", "ACCT-C5-OTHER"]


def test_account_vehicle_post_event_transfer_uses_event_time_owner(reader):
    finding = AccountVehicleSpecialist(reader).investigate("CASE-006")
    assert finding["correct_owner_count"] == 1
    assert finding["correct_owner_accounts"] == ["ACCT-C6-OLD"], \
        "half-open validity: owner at event time, not current owner"


# ------------------------------------------------------------ Billing/Notice
def test_billing_reports_both_sides_amounts(reader):
    finding = BillingNoticeSpecialist(reader).investigate("CASE-001")
    sides = finding["sides"]
    assert sides["ORIGINAL"]["charged"] == Decimal("12.50")
    assert sides["ORIGINAL"]["paid"] == Decimal("0.00")
    assert sides["CORRECTED"]["charged"] == Decimal("12.50")
    assert sides["CORRECTED"]["paid"] == Decimal("12.50")
    assert finding["both_accounts_charged"] is True
    assert finding["currency"] == "AUD"
    assert finding["evidence"]["notice_ids"] == ["NOTICE-C1", "NOTICE-W1"]


def test_billing_reports_wrong_side_payment(reader):
    finding = BillingNoticeSpecialist(reader).investigate("CASE-006")
    assert finding["sides"]["ORIGINAL"]["paid"] == Decimal("12.50")
    assert finding["sides"]["ORIGINAL"]["net_paid"] == Decimal("12.50")
    assert finding["wrong_side_net_paid"] == Decimal("12.50")


# ------------------------------------------------------------ Remediation
def test_remediation_detects_completed_refund(reader):
    finding = RemediationSpecialist(reader).investigate("CASE-002")
    assert finding["completed_refund"] is True
    assert finding["completed_write_off"] is False
    assert finding["evidence"]["remediation_ids"] == ["REM-W2"]


def test_remediation_detects_completed_write_off(reader):
    finding = RemediationSpecialist(reader).investigate("CASE-003")
    assert finding["completed_write_off"] is True
    assert finding["completed_refund"] is False


def test_remediation_reports_no_history(reader):
    finding = RemediationSpecialist(reader).investigate("CASE-001")
    assert finding["completed_refund"] is False
    assert finding["completed_write_off"] is False
    assert finding["evidence"].get("remediation_ids", []) == []


# ------------------------------------------------------------ orchestration
def test_investigate_case_returns_advisory_report_only(transport):
    report = investigate_case(AlprReadOnlyReader(transport), "CASE-002")
    assert report["case_id"] == "CASE-002"
    assert report["finding"] == "ALREADY_REFUNDED"
    assert report["recommendation"] == "NO_DUPLICATE_REFUND"
    assert report["advisory_only"] is True
    assert report["actions_executed"] == []
    assert report["policy_version"] == "DEMO-1"
    assert report["synthetic"] is True
    assert report["live"] is False, "aggregated report must carry the transport's live flag"
    # evidence references must be present and case-scoped
    assert any(e.startswith("REM-") for e in report["evidence"]["remediation_ids"])


def test_specialists_never_emit_writes_or_touch_gold(transport):
    for i in range(1, 7):
        investigate_case(AlprReadOnlyReader(transport), f"CASE-{i:03}")
    all_sql = " ".join(b["statement"].upper() for b in transport.submitted)
    for verb in ("INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "DROP", "GRANT", "CALL"):
        assert f"{verb} " not in all_sql.replace("USE ROLE", "").replace(
            "USE SECONDARY ROLES", "")
    assert "EXPECTED_CASE_RESULTS" not in all_sql
    assert "ALPR_DEMO." not in all_sql


def test_specialist_denied_propagates_fail_closed():
    bad = FakeSqlApiTransport(force_role="GAB_BOOTSTRAP_ROLE")
    with pytest.raises(AlprReaderDenied):
        investigate_case(AlprReadOnlyReader(bad), "CASE-001")
