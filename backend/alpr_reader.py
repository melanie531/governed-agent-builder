"""Fail-closed, read-only Snowflake reader for the ALPR investigation specialists.

Design contract (enforced here, pinned by tests/test_alpr_reader.py):

- Fixed SQL whitelist. The only data statement this module can emit is
  ``SELECT * FROM GAB_DEMO_DB.ALPR_APPROVED.<view> WHERE CASE_ID = '<key>'
  LIMIT n`` over exactly the four approved secure views. Per the official
  SQL API docs (sql-api/submitting-multiple-statements), bind variables are
  NOT supported in multi-statement requests, so the case key is embedded as
  a literal — but ONLY after strict ASCII ``CASE-[0-9]{3}`` validation, so
  no arbitrary SQL can ever reach the statement text. The gold
  expected-results table and the ALPR_DEMO base schema are unreachable by
  construction.
- Same-session role pinning. Snowflake's SQL API v2 treats each request as
  its own session: session state set by one request does NOT carry to the
  next request (a cross-request ``USE ROLE`` would silently not apply). So
  every fetch is ONE multi-statement request whose statements run in order in
  a single session: ``USE SECONDARY ROLES NONE`` then
  ``USE ROLE GAB_QUERY_READONLY`` then a verification SELECT of
  CURRENT_ROLE()/CURRENT_SECONDARY_ROLES() then the whitelisted data SELECT.
  The request body also carries ``role`` so the session starts with the
  reader role, and the verification result is checked BEFORE the data result
  is fetched: any mismatch denies without reading rows.
- Fail closed. No transport => denied. No fixture fallback, no bootstrap
  fallback, no default credentials. Budgets count every actual HTTP call
  (submit and each result fetch) against ``max_requests`` and enforce a
  wall-clock deadline before each call. Results split into more than one
  partition are denied loudly rather than silently truncated.
- Provenance. Every result carries the case/event/notice/evidence/ownership
  ids present in the rows, the policy version, and an explicit
  ``synthetic``/``live`` labelling (this reader never claims live unless the
  injected transport is the live SQL API transport, which this slice does not
  provide credentials for).

This module performs NO network I/O itself; the transport is injected. It
must never be constructed with the bootstrap discovery helper.
"""
import json
import re
import time

READER_ROLE = "GAB_QUERY_READONLY"
FORBIDDEN_ROLES = ("GAB_BOOTSTRAP_ROLE",)
APPROVED_SCHEMA = "GAB_DEMO_DB.ALPR_APPROVED"
APPROVED_VIEWS = ("ACCOUNT_VEHICLE", "BILLING_NOTICE", "REMEDIATION", "POLICY")
# re.ASCII pins [0-9] semantics even if the pattern ever changes to \d.
CASE_ID_PATTERN = re.compile(r"CASE-[0-9]{3}\Z", re.ASCII)

_VERIFY_SELECT = ("SELECT CURRENT_ROLE() AS ROLE_NAME, "
                  "CURRENT_SECONDARY_ROLES() AS SECONDARY_ROLES")

# id-bearing columns surfaced as provenance on every result
_ID_COLUMNS = {
    "event_ids": "EVENT_ID", "notice_ids": "NOTICE_ID", "evidence_ids": "EVIDENCE_ID",
    "correction_evidence_ids": "CORRECTION_EVIDENCE_ID", "ownership_ids": "OWNERSHIP_ID",
    "remediation_ids": "REMEDIATION_ID", "account_ids": "ACCOUNT_ID",
}


class AlprReaderDenied(Exception):
    """Raised whenever the fail-closed contract would be violated."""


def _secondary_roles_active(raw):
    """True unless CURRENT_SECONDARY_ROLES() proves an empty secondary-role set.

    Fail closed on anything but the documented shape with BOTH keys present
    and empty (the provisioned account really returns
    ``{"roles":"","value":""}``). Missing keys or an unknown structure count
    as active.
    """
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return True
    if not isinstance(parsed, dict) or set(parsed) != {"roles", "value"}:
        return True
    return bool(parsed["roles"]) or bool(parsed["value"])


class AlprReadOnlyReader:
    """Parameterized whitelist reader over the four approved ALPR views."""

    def __init__(self, transport, *, max_rows=200, max_requests=32,
                 max_seconds=60.0, clock=time.monotonic):
        if transport is None or not callable(getattr(transport, "submit", None)) \
                or not callable(getattr(transport, "fetch", None)):
            raise AlprReaderDenied("transport with submit/fetch required; no fixture "
                                   "or bootstrap fallback exists")
        self._transport = transport
        self._max_rows = int(max_rows)
        self._max_requests = int(max_requests)
        self._max_seconds = float(max_seconds)
        self._clock = clock
        self._started = clock()
        self._requests = 0

    # ------------------------------------------------------------ guards
    def _check_budgets(self):
        if self._requests >= self._max_requests:
            raise AlprReaderDenied(f"request budget exhausted ({self._max_requests})")
        if self._clock() - self._started > self._max_seconds:
            raise AlprReaderDenied(f"time budget exhausted ({self._max_seconds}s)")

    def _submit(self, body):
        """One real HTTP call: budget-checked and counted."""
        self._check_budgets()
        self._requests += 1
        return self._transport.submit(body)

    def _fetch_result(self, handle):
        """One real HTTP result fetch: budget-checked and counted."""
        self._check_budgets()
        self._requests += 1
        return self._transport.fetch(handle)

    @staticmethod
    def _validate(view, case_id):
        if view not in APPROVED_VIEWS:
            raise AlprReaderDenied(f"view not in approved whitelist: {view!r}")
        if not isinstance(case_id, str) or not CASE_ID_PATTERN.fullmatch(case_id):
            raise AlprReaderDenied(f"invalid case_id: {case_id!r}")

    # ------------------------------------------------------------ fetch
    def fetch(self, view, case_id):
        """Return rows of one approved view for one case, with provenance.

        One SQL API request = one session; role pinning, secondary-role
        clearing, verification and the data SELECT all travel together.
        """
        self._validate(view, case_id)
        # case_id passed strict ASCII CASE-[0-9]{3}: safe as a literal. The
        # official SQL API forbids bindings in multi-statement requests.
        select = (f"SELECT * FROM {APPROVED_SCHEMA}.{view} "
                  f"WHERE CASE_ID = '{case_id}' LIMIT {self._max_rows + 1}")
        body = {
            "statement": "; ".join([
                "USE SECONDARY ROLES NONE",
                f"USE ROLE {READER_ROLE}",
                _VERIFY_SELECT,
                select,
            ]),
            "role": READER_ROLE,
            "parameters": {"MULTI_STATEMENT_COUNT": "4"},
            "timeout": min(int(self._max_seconds), 60),
        }
        handles = self._submit(body).get("statementHandles") or []
        if len(handles) != 4:
            raise AlprReaderDenied(f"expected 4 statement handles, got {len(handles)}")

        # Verify session identity BEFORE touching the data result.
        verify = self._fetch_result(handles[2])
        vrows = verify.get("data") or []
        if not vrows:
            raise AlprReaderDenied("session verification returned no row; denying")
        role_name, secondary = vrows[0][0], vrows[0][1]
        if role_name != READER_ROLE or role_name in FORBIDDEN_ROLES:
            raise AlprReaderDenied(
                f"CURRENT_ROLE() is {role_name!r}, expected {READER_ROLE!r}; denying")
        if _secondary_roles_active(secondary):
            raise AlprReaderDenied(
                "CURRENT_SECONDARY_ROLES() reports active SECONDARY roles; denying")

        data = self._fetch_result(handles[3])
        meta = data.get("resultSetMetaData", {})
        partitions = meta.get("partitionInfo") or []
        if len(partitions) > 1:
            raise AlprReaderDenied(
                f"result split into {len(partitions)} partitions; refusing to "
                "silently drop rows (tighten LIMIT or raise budgets)")
        columns = [c["name"] for c in meta.get("rowType", [])]
        raw_rows = data.get("data") or []
        if len(raw_rows) > self._max_rows:
            raise AlprReaderDenied(f"row budget exceeded (> {self._max_rows})")
        rows = [dict(zip(columns, row)) for row in raw_rows]

        ids = {}
        for key, column in _ID_COLUMNS.items():
            values = sorted({row[column] for row in rows if row.get(column)})
            if values:
                ids[key] = values
        data_classes = {row.get("DATA_CLASS") for row in rows if "DATA_CLASS" in row}
        policy_versions = sorted({row["POLICY_VERSION"] for row in rows
                                  if row.get("POLICY_VERSION")})
        return {
            "view": view,
            "case_id": case_id,
            "rows": rows,
            "row_count": len(rows),
            "ids": ids,
            "policy_version": policy_versions[0] if policy_versions else None,
            "synthetic": (data_classes == {"SYNTHETIC"}) if data_classes else True,
            "live": bool(getattr(self._transport, "is_live", False)),
            "role_verified": READER_ROLE,
            "secondary_roles_verified_none": True,
        }
