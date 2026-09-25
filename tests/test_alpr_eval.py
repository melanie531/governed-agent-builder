"""Tests for the ALPR six-case evaluation runner.

OFFLINE / NOT LIVE: the runner is exercised against the offline fixture
transport. Expected results (gold) live ONLY in the eval module — the reader
and specialists have no access path to them.

Pins: per-dimension deterministic checks (facts / bilateral amounts /
remediation history / event attribution / evidence citation / recommendation
/ unresolved handling), no keyword-only PASS, and gold isolation.
"""
import inspect
from decimal import Decimal

import pytest

import backend.alpr_reader as reader_mod
import backend.alpr_specialists as spec_mod
from backend.alpr_eval import EXPECTED, evaluate_case, run_eval
from backend.alpr_reader import AlprReadOnlyReader
from backend.alpr_specialists import investigate_case
from tests.alpr_fixture import FakeSqlApiTransport


def report_for(case_id):
    return investigate_case(AlprReadOnlyReader(FakeSqlApiTransport()), case_id)


def test_gold_lives_only_on_the_eval_side():
    for module in (reader_mod, spec_mod):
        source = inspect.getsource(module)
        assert "EXPECTED_CASE_RESULTS" not in source.replace(
            "outside the approved views", "")  # comments may explain, not query
        assert "alpr_eval" not in source, "runtime code must not import the eval module"
    assert set(EXPECTED) == {f"CASE-{i:03}" for i in range(1, 7)}


def test_all_six_cases_pass_against_gold():
    results = run_eval(lambda case_id: report_for(case_id))
    assert results["not_live"] is True
    assert results["synthetic"] is True
    assert results["total"] == 6
    failed = {c: r for c, r in results["cases"].items() if not r["passed"]}
    assert not failed, failed
    assert results["passed"] == 6


def test_dimensions_are_scored_separately():
    result = evaluate_case("CASE-001", report_for("CASE-001"))
    dims = result["dimensions"]
    assert set(dims) == {"owner_attribution", "bilateral_amounts", "remediation_history",
                         "finding", "recommendation", "evidence_citation",
                         "unresolved_handling"}
    assert all(d["passed"] for d in dims.values()), dims


def test_keyword_match_alone_cannot_pass():
    """A report whose finding string matches but whose facts are wrong must FAIL."""
    report = report_for("CASE-001")
    report["specialists"]["account_vehicle"]["correct_owner_count"] = 2  # wrong fact
    result = evaluate_case("CASE-001", report)
    assert result["dimensions"]["finding"]["passed"] is True  # keyword still matches
    assert result["dimensions"]["owner_attribution"]["passed"] is False
    assert result["passed"] is False


def test_bilateral_amounts_checked_as_decimals_not_strings():
    report = report_for("CASE-001")
    report["specialists"]["billing_notice"]["sides"]["CORRECTED"]["paid"] = Decimal("12.49")
    result = evaluate_case("CASE-001", report)
    assert result["dimensions"]["bilateral_amounts"]["passed"] is False


def test_remediation_history_dimension_catches_missing_dedup():
    report = report_for("CASE-002")
    report["specialists"]["remediation"]["completed_refund"] = False
    result = evaluate_case("CASE-002", report)
    assert result["dimensions"]["remediation_history"]["passed"] is False


def test_evidence_citation_requires_case_scoped_ids():
    report = report_for("CASE-003")
    report["evidence"]["notice_ids"] = []
    report["evidence"]["event_ids"] = []
    result = evaluate_case("CASE-003", report)
    assert result["dimensions"]["evidence_citation"]["passed"] is False


def test_unresolved_cases_require_manual_review_and_no_action():
    for case_id in ("CASE-004", "CASE-005"):
        result = evaluate_case(case_id, report_for(case_id))
        assert result["dimensions"]["unresolved_handling"]["passed"] is True
    # a report that recommends financial action on an unresolved case must fail
    report = report_for("CASE-004")
    report["recommendation"] = "REVIEW_REFUND_12_50_USE_OLD_OWNER"
    report["finding"] = "MISSING_EVENT_TIME_OWNER"
    result = evaluate_case("CASE-004", report)
    assert result["dimensions"]["unresolved_handling"]["passed"] is False
    assert result["passed"] is False


def test_executed_actions_always_fail_the_eval():
    report = report_for("CASE-006")
    report["actions_executed"] = ["REFUND NOTICE-W6"]
    result = evaluate_case("CASE-006", report)
    assert result["passed"] is False
    assert result["dimensions"]["unresolved_handling"]["passed"] is False


def test_unknown_case_is_rejected():
    with pytest.raises(KeyError):
        evaluate_case("CASE-999", report_for("CASE-001"))
