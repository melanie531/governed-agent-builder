"""ALPR investigation specialists: controlled reads, evidence + advice only.

Three specialists (Account/Vehicle, Billing/Notice, Remediation) plus a case
orchestrator. Every data access goes through the injected
AlprReadOnlyReader — the fail-closed, whitelist-only reader — so specialists
cannot issue arbitrary SQL, cannot write, and cannot reach the gold
expected-results table (it lives outside the approved views by provisioning
and outside the reader by construction).

Specialists return findings, evidence references (case/event/notice/
remediation/ownership/evidence IDs) and ADVISORY recommendations only.
``actions_executed`` is always empty: no refund, write-off, notice or any
other write is ever performed by this code. Policy DEMO-1 is a demo
assumption, not customer-approved policy; results carry the policy version
and the synthetic label from the reader.
"""
from decimal import Decimal

# Advisory recommendation vocabulary (policy DEMO-1 demo assumptions).
REC_REVIEW_WRONG_UNPAID = "REVIEW_WRONG_UNPAID_NOTICE_NO_SECOND_CORRECT_CHARGE"
REC_NO_DUPLICATE_REFUND = "NO_DUPLICATE_REFUND"
REC_NO_DUPLICATE_WRITEOFF = "NO_DUPLICATE_WRITEOFF"
REC_MANUAL_REVIEW = "MANUAL_REVIEW_NO_FINANCIAL_ACTION"
REC_REVIEW_REFUND_OLD_OWNER = "REVIEW_REFUND_12_50_USE_OLD_OWNER"


def _dec(value):
    return Decimal(value if value is not None else "0")


class _Specialist:
    view = None

    def __init__(self, reader):
        self._reader = reader

    def _rows(self, case_id):
        result = self._reader.fetch(self.view, case_id)
        return result["rows"], result


class AccountVehicleSpecialist(_Specialist):
    """Event-time ownership attribution over the ACCOUNT_VEHICLE view."""

    view = "ACCOUNT_VEHICLE"

    def investigate(self, case_id):
        rows, result = self._rows(case_id)
        corrected = [r for r in rows if r.get("ATTRIBUTION_SIDE") == "CORRECTED"]
        owners = sorted({r["ACCOUNT_ID"] for r in corrected if r.get("ACCOUNT_ID")})
        count = len(owners)
        if count == 0:
            attribution = "MISSING_EVENT_TIME_OWNER"
        elif count == 1:
            attribution = "SINGLE_CORRECTED_OWNER"
        else:
            attribution = "CONFLICTING_EVENT_TIME_OWNERS"
        return {
            "specialist": "account_vehicle",
            "case_id": case_id,
            "correct_owner_count": count,
            "correct_owner_accounts": owners,
            "attribution": attribution,
            "evidence": result["ids"],
            "synthetic": result["synthetic"],
        }


class BillingNoticeSpecialist(_Specialist):
    """Bilateral charge/payment amounts over the BILLING_NOTICE view."""

    view = "BILLING_NOTICE"

    def investigate(self, case_id):
        rows, result = self._rows(case_id)
        sides = {}
        for side in ("ORIGINAL", "CORRECTED"):
            side_rows = [r for r in rows if r.get("ATTRIBUTION_SIDE") == side]
            sides[side] = {
                "notice_ids": sorted(r["NOTICE_ID"] for r in side_rows),
                "accounts": sorted({r["ACCOUNT_ID"] for r in side_rows}),
                "charged": sum((_dec(r["CHARGE_AMOUNT"]) for r in side_rows), Decimal("0.00")),
                "paid": sum((_dec(r["PAID_AMOUNT"]) for r in side_rows), Decimal("0.00")),
                "refunded": sum((_dec(r["REFUNDED_AMOUNT"]) for r in side_rows), Decimal("0.00")),
                "written_off": sum((_dec(r["WRITTEN_OFF_AMOUNT"]) for r in side_rows), Decimal("0.00")),
                "net_paid": sum((_dec(r["NET_PAID_AMOUNT"]) for r in side_rows), Decimal("0.00")),
                "unpaid_balance": sum((_dec(r["UNPAID_BALANCE_BEFORE_REFUND_ADJUSTMENT"])
                                       for r in side_rows), Decimal("0.00")),
            }
        currencies = sorted({r["CURRENCY"] for r in rows if r.get("CURRENCY")})
        return {
            "specialist": "billing_notice",
            "case_id": case_id,
            "sides": sides,
            "both_accounts_charged": bool(sides["ORIGINAL"]["notice_ids"]
                                          and sides["CORRECTED"]["notice_ids"]),
            "wrong_side_net_paid": sides["ORIGINAL"]["net_paid"],
            "currency": currencies[0] if len(currencies) == 1 else currencies,
            "evidence": result["ids"],
            "synthetic": result["synthetic"],
        }


class RemediationSpecialist(_Specialist):
    """Completed refund/write-off history over the REMEDIATION view."""

    view = "REMEDIATION"

    def investigate(self, case_id):
        rows, result = self._rows(case_id)
        completed = [r for r in rows if r.get("STATUS") == "COMPLETED"]
        refunds = [r for r in completed if r.get("ACTION") == "REFUND"]
        write_offs = [r for r in completed if r.get("ACTION") == "WRITE_OFF"]
        evidence = dict(result["ids"])
        evidence.setdefault("remediation_ids", [])
        return {
            "specialist": "remediation",
            "case_id": case_id,
            "completed_refund": bool(refunds),
            "completed_write_off": bool(write_offs),
            "refund_amount": sum((_dec(r["AMOUNT"]) for r in refunds), Decimal("0.00")),
            "write_off_amount": sum((_dec(r["AMOUNT"]) for r in write_offs), Decimal("0.00")),
            "evidence": evidence,
            "synthetic": result["synthetic"],
        }


def _classify(ownership, billing, remediation):
    """Map specialist findings to (finding, recommendation) under DEMO-1 rules.

    DEMO-1 (demo assumption, not customer policy): event-time ownership with
    half-open intervals; investigate both accounts; missing/conflicting
    ownership => manual review; never duplicate completed refunds/write-offs;
    recommend review only, never execute.
    """
    if ownership["attribution"] == "MISSING_EVENT_TIME_OWNER":
        return "MISSING_EVENT_TIME_OWNER", REC_MANUAL_REVIEW
    if ownership["attribution"] == "CONFLICTING_EVENT_TIME_OWNERS":
        return "CONFLICTING_EVENT_TIME_OWNERS", REC_MANUAL_REVIEW
    if remediation["completed_refund"]:
        return "ALREADY_REFUNDED", REC_NO_DUPLICATE_REFUND
    if remediation["completed_write_off"]:
        return "ALREADY_WRITTEN_OFF", REC_NO_DUPLICATE_WRITEOFF
    if billing["wrong_side_net_paid"] > 0:
        return "POST_EVENT_TRANSFER_WRONG_PAYMENT", REC_REVIEW_REFUND_OLD_OWNER
    if billing["both_accounts_charged"]:
        return "BOTH_ACCOUNTS_CHARGED", REC_REVIEW_WRONG_UNPAID
    return "UNRESOLVED", REC_MANUAL_REVIEW


def investigate_case(reader, case_id):
    """Run all three specialists plus the policy view; return an advisory report."""
    ownership = AccountVehicleSpecialist(reader).investigate(case_id)
    billing = BillingNoticeSpecialist(reader).investigate(case_id)
    remediation = RemediationSpecialist(reader).investigate(case_id)
    policy = reader.fetch("POLICY", case_id)
    finding, recommendation = _classify(ownership, billing, remediation)
    evidence = {}
    for part in (ownership, billing, remediation):
        for key, values in part["evidence"].items():
            evidence[key] = sorted(set(evidence.get(key, [])) | set(values))
    evidence.setdefault("remediation_ids", [])
    return {
        "case_id": case_id,
        "finding": finding,
        "recommendation": recommendation,
        "advisory_only": True,
        "actions_executed": [],  # this system never executes refunds/notices
        "specialists": {
            "account_vehicle": ownership,
            "billing_notice": billing,
            "remediation": remediation,
        },
        "evidence": evidence,
        "policy_version": policy["policy_version"],
        "policy_approval_status": (policy["rows"][0]["APPROVAL_STATUS"]
                                   if policy["rows"] else None),
        "synthetic": all(p["synthetic"] for p in (ownership, billing, remediation)),
    }
