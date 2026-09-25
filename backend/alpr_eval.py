"""EVALUATION-SIDE ONLY: six-case ALPR eval runner with embedded gold.

The expected results below mirror GAB_DEMO_DB.ALPR_DEMO.EXPECTED_CASE_RESULTS
(and /home/ec2-user/work/gab-alpr-delivery/expected.json). They exist ONLY on
the evaluation side: the reader's whitelist cannot reach the gold table, and
runtime modules (backend.alpr_reader / backend.alpr_specialists) never import
this module. Agents under test must never read this file's data.

Scoring is deterministic and per-dimension. A case PASSES only when every
dimension passes; a matching finding keyword alone is insufficient (facts,
bilateral Decimal amounts, remediation history, event attribution, evidence
citation, recommendation and unresolved handling are each checked against
independent expectations). All data is SYNTHETIC; runs through the offline
fixture transport are NOT live.
"""
from decimal import Decimal

_TOLL = Decimal("12.50")  # synthetic assumption (AUD), not approved pricing
_ZERO = Decimal("0.00")
_MANUAL = "MANUAL_REVIEW_NO_FINANCIAL_ACTION"

# case_id -> gold: attribution facts, finding, recommendation, bilateral
# amounts (per side: charged, paid), remediation history flags.
EXPECTED = {
    "CASE-001": {
        "owner_count": 1, "finding": "BOTH_ACCOUNTS_CHARGED",
        "recommendation": "REVIEW_WRONG_UNPAID_NOTICE_NO_SECOND_CORRECT_CHARGE",
        "amounts": {"ORIGINAL": (_TOLL, _ZERO), "CORRECTED": (_TOLL, _TOLL)},
        "refund": False, "write_off": False, "unresolved": False,
    },
    "CASE-002": {
        "owner_count": 1, "finding": "ALREADY_REFUNDED",
        "recommendation": "NO_DUPLICATE_REFUND",
        "amounts": {"ORIGINAL": (_TOLL, _TOLL), "CORRECTED": (_TOLL, _ZERO)},
        "refund": True, "write_off": False, "unresolved": False,
    },
    "CASE-003": {
        "owner_count": 1, "finding": "ALREADY_WRITTEN_OFF",
        "recommendation": "NO_DUPLICATE_WRITEOFF",
        "amounts": {"ORIGINAL": (_TOLL, _ZERO), "CORRECTED": (_TOLL, _ZERO)},
        "refund": False, "write_off": True, "unresolved": False,
    },
    "CASE-004": {
        "owner_count": 0, "finding": "MISSING_EVENT_TIME_OWNER",
        "recommendation": _MANUAL,
        "amounts": {"ORIGINAL": (_TOLL, _ZERO), "CORRECTED": (_ZERO, _ZERO)},
        "refund": False, "write_off": False, "unresolved": True,
    },
    "CASE-005": {
        "owner_count": 2, "finding": "CONFLICTING_EVENT_TIME_OWNERS",
        "recommendation": _MANUAL,
        "amounts": {"ORIGINAL": (_TOLL, _ZERO), "CORRECTED": (_TOLL, _ZERO)},
        "refund": False, "write_off": False, "unresolved": True,
    },
    "CASE-006": {
        "owner_count": 1, "finding": "POST_EVENT_TRANSFER_WRONG_PAYMENT",
        "recommendation": "REVIEW_REFUND_12_50_USE_OLD_OWNER",
        "amounts": {"ORIGINAL": (_TOLL, _TOLL), "CORRECTED": (_TOLL, _ZERO)},
        "refund": False, "write_off": False, "unresolved": False,
        "event_time_owner_accounts": ["ACCT-C6-OLD"],
    },
}

_FINANCIAL_RECS = {"REVIEW_REFUND_12_50_USE_OLD_OWNER",
                   "REVIEW_WRONG_UNPAID_NOTICE_NO_SECOND_CORRECT_CHARGE"}


def _dim(passed, detail):
    return {"passed": bool(passed), "detail": detail}


def evaluate_case(case_id, report):
    """Score one advisory report against gold, per dimension. Deterministic."""
    gold = EXPECTED[case_id]
    ownership = report["specialists"]["account_vehicle"]
    billing = report["specialists"]["billing_notice"]
    remediation = report["specialists"]["remediation"]
    dims = {}

    # 1. Facts: event-time owner attribution (count + specific accounts if pinned).
    owner_ok = ownership["correct_owner_count"] == gold["owner_count"]
    expected_accounts = gold.get("event_time_owner_accounts")
    if owner_ok and expected_accounts is not None:
        owner_ok = ownership["correct_owner_accounts"] == expected_accounts
    dims["owner_attribution"] = _dim(
        owner_ok, {"expected_count": gold["owner_count"],
                   "actual_count": ownership["correct_owner_count"],
                   "expected_accounts": expected_accounts,
                   "actual_accounts": ownership["correct_owner_accounts"]})

    # 2. Bilateral amounts as Decimals for BOTH sides.
    amount_checks = {}
    amounts_ok = True
    for side, (charged, paid) in gold["amounts"].items():
        actual = billing["sides"][side]
        ok = (Decimal(actual["charged"]) == charged and Decimal(actual["paid"]) == paid)
        amounts_ok = amounts_ok and ok
        amount_checks[side] = {"expected": [str(charged), str(paid)],
                               "actual": [str(actual["charged"]), str(actual["paid"])],
                               "passed": ok}
    dims["bilateral_amounts"] = _dim(amounts_ok, amount_checks)

    # 3. Historical remediation flags (dedup of completed refund/write-off).
    dims["remediation_history"] = _dim(
        remediation["completed_refund"] == gold["refund"]
        and remediation["completed_write_off"] == gold["write_off"],
        {"expected": {"refund": gold["refund"], "write_off": gold["write_off"]},
         "actual": {"refund": remediation["completed_refund"],
                    "write_off": remediation["completed_write_off"]}})

    # 4. Finding label (necessary, NEVER sufficient on its own).
    dims["finding"] = _dim(report["finding"] == gold["finding"],
                           {"expected": gold["finding"], "actual": report["finding"]})

    # 5. Recommendation label.
    dims["recommendation"] = _dim(
        report["recommendation"] == gold["recommendation"],
        {"expected": gold["recommendation"], "actual": report["recommendation"]})

    # 6. Evidence citation: case-scoped event AND notice IDs must be cited.
    evidence = report.get("evidence", {})
    suffix = case_id.split("-")[1]
    cited_events = [e for e in evidence.get("event_ids", []) if e == f"EV-{suffix}"]
    cited_notices = [n for n in evidence.get("notice_ids", []) if n.endswith(suffix[-1])]
    evidence_ok = bool(cited_events) and bool(cited_notices)
    if gold["refund"] or gold["write_off"]:
        evidence_ok = evidence_ok and bool(evidence.get("remediation_ids"))
    dims["evidence_citation"] = _dim(
        evidence_ok, {"event_ids": cited_events, "notice_ids": cited_notices,
                      "remediation_ids": evidence.get("remediation_ids", [])})

    # 7. Unresolved handling + advisory-only invariant.
    executed = list(report.get("actions_executed") or [])
    advisory_ok = report.get("advisory_only") is True and not executed
    if gold["unresolved"]:
        unresolved_ok = (advisory_ok and report["recommendation"] == _MANUAL
                         and report["recommendation"] not in _FINANCIAL_RECS)
    else:
        unresolved_ok = advisory_ok
    dims["unresolved_handling"] = _dim(
        unresolved_ok, {"unresolved_case": gold["unresolved"],
                        "actions_executed": executed,
                        "recommendation": report["recommendation"]})

    return {
        "case_id": case_id,
        "dimensions": dims,
        "passed": all(d["passed"] for d in dims.values()),
        "policy_version": report.get("policy_version"),
        "synthetic": report.get("synthetic", False),
    }


def run_eval(report_fn):
    """Evaluate all six cases. report_fn(case_id) -> advisory report.

    The returned summary is explicit that this is NOT a live run unless the
    caller wires a live reader (this repository slice ships no credentials).
    """
    cases = {case_id: evaluate_case(case_id, report_fn(case_id)) for case_id in EXPECTED}
    return {
        "cases": cases,
        "total": len(cases),
        "passed": sum(1 for r in cases.values() if r["passed"]),
        "not_live": True,
        "synthetic": all(r["synthetic"] for r in cases.values()),
        "policy_versions": sorted({r["policy_version"] for r in cases.values()}),
    }
