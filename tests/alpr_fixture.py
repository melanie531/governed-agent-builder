"""Offline, SQL-API-shaped fixture for ALPR reader/specialist/eval tests.

NOT LIVE. Nothing in this module opens a network connection or touches
Snowflake. The rows mirror what the provisioned secure views
GAB_DEMO_DB.ALPR_APPROVED.{ACCOUNT_VEHICLE,BILLING_NOTICE,REMEDIATION,POLICY}
return for the six synthetic cases (CASE-001..CASE-006), in the same
result-set shape the Snowflake SQL API v2 uses (rowType metadata + data as
lists of strings/None). The gold table EXPECTED_CASE_RESULTS is deliberately
NOT modelled here: the runtime reader must never be able to see it.

The FakeSqlApiTransport emulates the documented SQL API session semantics
that matter for the fail-closed reader design:
- every submit() is its own session (no state carries across requests);
- a multi-statement request executes its statements in ONE session, so
  USE ROLE / USE SECONDARY ROLES NONE apply to the later statements of the
  SAME request only;
- submit returns statementHandles; each statement's result is fetched
  separately.
"""
import copy
from decimal import Decimal

EVENT_AT = "2026-08-10 10:00:00"
SCENARIOS = {
    1: "Both accounts charged",
    2: "Wrong account paid; refund already completed",
    3: "Wrong charge already written off",
    4: "Missing ownership at event time",
    5: "Conflicting event-time ownership records",
    6: "Ownership transfer after event; wrong payment refundable",
}
RULE_TEXT = ("Use event-time ownership with half-open validity intervals. Investigate both "
             "accounts. Missing/conflicting ownership requires manual review. Never duplicate "
             "completed refunds or write-offs. Recommend reversal/refund review only; no "
             "execution. Currency AUD and 12.50 toll are synthetic assumptions, not approved pricing.")


def _t(name, type_="text", scale=0):
    return {"name": name, "type": type_, "scale": scale}


# ---------------------------------------------------------------- ACCOUNT_VEHICLE
AV_TYPES = [
    _t("CASE_ID"), _t("SCENARIO"), _t("DATA_CLASS"), _t("EVENT_ID"),
    _t("EVENT_AT", "timestamp_ntz"), _t("ORIGINAL_PLATE"), _t("CORRECTED_PLATE"),
    _t("CORRECTION_EVIDENCE_ID"), _t("CORRECTION_STATUS"), _t("OWNERSHIP_ID"),
    _t("VEHICLE_ID"), _t("ACCOUNT_ID"), _t("VALID_FROM", "timestamp_ntz"),
    _t("VALID_TO", "timestamp_ntz"), _t("RECORD_SOURCE"), _t("ATTRIBUTION_SIDE"),
]


def _av(i, own, veh, acct, vfrom, vto, src, side):
    return [f"CASE-{i:03}", SCENARIOS[i], "SYNTHETIC", f"EV-{i:03}", EVENT_AT,
            f"SYN-W{i}", f"SYN-C{i}", f"REVIEW-{i:03}", "HUMAN_REVIEWED_SYNTHETIC",
            own, veh, acct, vfrom, vto, src, side]


AV_ROWS = (
    [_av(i, f"OWN-W{i}", f"VEH-W{i}", f"ACCT-W{i}", "2026-01-01 00:00:00", None,
         "SYNTHETIC_REGISTER", "ORIGINAL") for i in range(1, 7)]
    + [_av(i, f"OWN-C{i}", f"VEH-C{i}", f"ACCT-C{i}", "2026-01-01 00:00:00", None,
           "SYNTHETIC_REGISTER", "CORRECTED") for i in (1, 2, 3, 5)]
    + [_av(5, "OWN-C5-CONFLICT", "VEH-C5", "ACCT-C5-OTHER", "2026-08-01 00:00:00", None,
           "SYNTHETIC_CONFLICTING_REGISTER", "CORRECTED"),
       _av(6, "OWN-C6-OLD", "VEH-C6", "ACCT-C6-OLD", "2026-01-01 00:00:00",
           "2026-08-15 00:00:00", "SYNTHETIC_REGISTER", "CORRECTED")]
)

# ---------------------------------------------------------------- BILLING_NOTICE
BN_TYPES = [
    _t("CASE_ID"), _t("NOTICE_ID"), _t("EVENT_ID"), _t("ACCOUNT_ID"),
    _t("ATTRIBUTION_SIDE"), _t("CHARGE_AMOUNT", "fixed", 2), _t("CURRENCY"),
    _t("ISSUED_AT", "timestamp_ntz"), _t("NOTICE_STATUS"),
    _t("PAID_AMOUNT", "fixed", 2), _t("REFUNDED_AMOUNT", "fixed", 2),
    _t("WRITTEN_OFF_AMOUNT", "fixed", 2), _t("NET_PAID_AMOUNT", "fixed", 2),
    _t("UNPAID_BALANCE_BEFORE_REFUND_ADJUSTMENT", "fixed", 2),
]


def _bn(i, notice, acct, side, paid="0.00", refunded="0.00", woff="0.00"):
    charge = Decimal("12.50")
    paid_d, ref_d, woff_d = Decimal(paid), Decimal(refunded), Decimal(woff)
    net = paid_d - ref_d
    unpaid = charge - paid_d - woff_d
    return [f"CASE-{i:03}", notice, f"EV-{i:03}", acct, side, "12.50", "AUD",
            "2026-08-11 00:00:00", "ISSUED", f"{paid_d:.2f}", f"{ref_d:.2f}",
            f"{woff_d:.2f}", f"{net:.2f}", f"{unpaid:.2f}"]


BN_ROWS = [
    _bn(1, "NOTICE-W1", "ACCT-W1", "ORIGINAL"),
    _bn(2, "NOTICE-W2", "ACCT-W2", "ORIGINAL", paid="12.50", refunded="12.50"),
    _bn(3, "NOTICE-W3", "ACCT-W3", "ORIGINAL", woff="12.50"),
    _bn(4, "NOTICE-W4", "ACCT-W4", "ORIGINAL"),
    _bn(5, "NOTICE-W5", "ACCT-W5", "ORIGINAL"),
    _bn(6, "NOTICE-W6", "ACCT-W6", "ORIGINAL", paid="12.50"),
    _bn(1, "NOTICE-C1", "ACCT-C1", "CORRECTED", paid="12.50"),
    _bn(2, "NOTICE-C2", "ACCT-C2", "CORRECTED"),
    _bn(3, "NOTICE-C3", "ACCT-C3", "CORRECTED"),
    _bn(5, "NOTICE-C5", "ACCT-C5", "CORRECTED"),
    _bn(6, "NOTICE-C6", "ACCT-C6-OLD", "CORRECTED"),
]

# ---------------------------------------------------------------- REMEDIATION
REM_TYPES = [
    _t("CASE_ID"), _t("NOTICE_ID"), _t("ACCOUNT_ID"), _t("ATTRIBUTION_SIDE"),
    _t("REMEDIATION_ID"), _t("ACTION"), _t("AMOUNT", "fixed", 2),
    _t("ACTION_AT", "timestamp_ntz"), _t("STATUS"), _t("EVIDENCE_ID"),
]
_REM_BY_NOTICE = {
    "NOTICE-W2": ["REM-W2", "REFUND", "12.50", "2026-08-13 00:00:00", "COMPLETED",
                  "SYNTHETIC-REFUND-RECEIPT"],
    "NOTICE-W3": ["REM-W3", "WRITE_OFF", "12.50", "2026-08-13 00:00:00", "COMPLETED",
                  "SYNTHETIC-WRITEOFF-RECEIPT"],
}
# (CASE_ID, NOTICE_ID, ACCOUNT_ID, ATTRIBUTION_SIDE) + remediation columns (LEFT JOIN)
REM_ROWS = [[r[0], r[1], r[3], r[4]] + _REM_BY_NOTICE.get(r[1], [None] * 6) for r in BN_ROWS]

# ---------------------------------------------------------------- POLICY
POL_TYPES = [
    _t("CASE_ID"), _t("POLICY_VERSION"), _t("EFFECTIVE_FROM", "timestamp_ntz"),
    _t("EFFECTIVE_TO", "timestamp_ntz"), _t("APPROVAL_STATUS"), _t("RULE_TEXT"),
]
POL_ROWS = [[f"CASE-{i:03}", "DEMO-1", "2026-01-01 00:00:00", None,
             "DEMO_ASSUMPTION_NOT_CUSTOMER_APPROVED", RULE_TEXT] for i in range(1, 7)]


def fixture_views():
    """Fresh, mutation-safe copy of the four approved views (and ONLY those)."""
    return copy.deepcopy({
        "ACCOUNT_VEHICLE": {"types": AV_TYPES, "rows": AV_ROWS},
        "BILLING_NOTICE": {"types": BN_TYPES, "rows": BN_ROWS},
        "REMEDIATION": {"types": REM_TYPES, "rows": REM_ROWS},
        "POLICY": {"types": POL_TYPES, "rows": POL_ROWS},
    })


SECONDARY_NONE = '{"roles":"","value":""}'
SECONDARY_ALL = '{"roles":"ALL","value":"ALL"}'


class FakeSqlApiTransport:
    """Offline stand-in for the Snowflake SQL API v2 (NOT live, no network).

    Session semantics emulated: state set by USE statements applies only to
    later statements of the SAME submit() request; nothing persists across
    submits (matching documented SQL API behaviour: each request is its own
    session unless an explicit session is requested).
    """

    def __init__(self, views=None, *, force_role=None, force_secondary=None):
        self.views = views if views is not None else fixture_views()
        self.force_role = force_role
        self.force_secondary = force_secondary
        self.submitted = []
        self.calls = 0
        self._responses = {}

    def _view_of(self, stmt):
        upper = stmt.upper()
        for name in self.views:
            if f"GAB_DEMO_DB.ALPR_APPROVED.{name} " in upper + " " or upper.rstrip().endswith(name):
                if f".{name}" in upper:
                    return name
        raise AssertionError(f"statement references no approved view: {stmt}")

    def submit(self, body):
        self.calls += 1
        self.submitted.append(body)
        stmts = [s.strip() for s in body.get("statement", "").split(";") if s.strip()]
        role = body.get("role")
        secondary = SECONDARY_ALL  # fresh session default: secondary roles not proven off
        handles = []
        for idx, stmt in enumerate(stmts):
            upper = stmt.upper()
            if upper.startswith("USE ROLE "):
                role = stmt.split()[2]
            if upper == "USE SECONDARY ROLES NONE":
                secondary = SECONDARY_NONE
            handle = f"h-{self.calls}-{idx}"
            if "CURRENT_ROLE()" in upper:
                resp = {"resultSetMetaData": {"rowType": [_t("ROLE_NAME"), _t("SECONDARY_ROLES")]},
                        "data": [[self.force_role or role, self.force_secondary or secondary]]}
            elif upper.startswith("SELECT") and " FROM " in upper:
                view = self._view_of(stmt)
                case_id = body["bindings"]["1"]["value"]
                rows = [list(r) for r in self.views[view]["rows"] if r[0] == case_id]
                resp = {"resultSetMetaData": {"rowType": self.views[view]["types"]}, "data": rows}
            else:
                resp = {"resultSetMetaData": {"rowType": [_t("status")]},
                        "data": [["Statement executed successfully."]]}
            self._responses[handle] = resp
            handles.append(handle)
        return {"statementHandles": handles}

    def fetch(self, handle):
        self.calls += 1
        return self._responses[handle]
