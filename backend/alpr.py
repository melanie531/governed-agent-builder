"""ALPR misread-plate investigation specialists over governed Snowflake views. READ-ONLY.

Rows come ONLY from the secure views in GAB_DEMO_DB.ALPR_INVESTIGATION_APPROVED (provisioned by
scripts/alpr_snowflake_provision.py; every row is SYNTHETIC and every billing policy rule is a
DEMO ASSUMPTION). One fixed statement per view with CASE_ID as a bind value; no SQL input.
Row source (ALPR_VIEW_SOURCE):
  snapshot  default. Verbatim capture of the live views (alpr_views_snapshot.json, see its
            provenance) so tests and the fixture runner stay network-free.
  live      query Snowflake as GAB_QUERY_READONLY with secondary roles forced off. Needs
            ALPR_SNOWFLAKE_ACCOUNT, ALPR_SNOWFLAKE_USER, ALPR_SNOWFLAKE_KEY_PATH. Fails closed;
            never falls back to the snapshot.
Findings are recommendations for human review: nothing here issues refunds, voids or notices.
Capture: ALPR_SNOWFLAKE_...=... uv run --with snowflake-connector-python --with cryptography python -m backend.alpr
"""
import datetime
import decimal
import json
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

SCHEMA = "GAB_DEMO_DB.ALPR_INVESTIGATION_APPROVED"
ROLE, WAREHOUSE = "GAB_QUERY_READONLY", "GAB_QUERY_WH"
VIEWS = {
    "ownership_at_event": (f"{SCHEMA}.VW_OWNERSHIP_AT_EVENT", "PLATE_ROLE, ACCOUNT_ID"),
    "charges_notices_payments": (f"{SCHEMA}.VW_CHARGES_NOTICES_PAYMENTS", "CHARGE_ID"),
    "remediation_status": (f"{SCHEMA}.VW_REMEDIATION_STATUS", "CHARGE_ID, REMEDIATION_ID"),
    "policy_at_event": (f"{SCHEMA}.VW_POLICY_AT_EVENT", "POLICY_ID, VERSION"),
}
CASE_ID = re.compile(r"^ALPR-C\d{3}$")
ROW_LIMIT = 200
LOGIN_TIMEOUT, NETWORK_TIMEOUT, STATEMENT_TIMEOUT = 15, 60, 60
SNAPSHOT = Path(__file__).with_name("alpr_views_snapshot.json")
ZERO = decimal.Decimal("0")


class ViewUnavailable(Exception):
    pass


def case_id_in(question: str):
    match = re.search(r"\bALPR-C\d{3}\b", question.upper())
    return match.group(0) if match else None


def normalize(value):
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value


_live = {}
_query_ids = ContextVar("alpr_query_ids", default=())
_live_access = ContextVar("alpr_live_access", default=None)


@contextmanager
def live_access(authorize, deadline):
    """Live MCP call boundary. Auth is server-owned and rechecked for every view."""
    token = _live_access.set((authorize, deadline))
    try:
        yield
    finally:
        _live_access.reset(token)


def live_timeout(ceiling):
    access = _live_access.get()
    remaining = min(ceiling, access[1] - time.monotonic()) if access else ceiling
    if remaining < 1:
        raise ViewUnavailable("ALPR_CALL_DEADLINE_EXCEEDED")
    return int(remaining)


@contextmanager
def query_evidence():
    """Isolate one connector call, restoring its caller's evidence on every exit."""
    token = _query_ids.set(())
    try:
        yield
    finally:
        _query_ids.reset(token)


def _ssm_client():
    import boto3
    if _live_access.get():
        from botocore.config import Config
        return boto3.client("ssm", region_name="us-west-2", config=Config(
            connect_timeout=live_timeout(3), read_timeout=live_timeout(3), retries={"total_max_attempts": 1}))
    return boto3.client("ssm")


def private_key_der():
    """Key material stays in memory only; never logged, echoed or baked into artifacts."""
    from cryptography.hazmat.primitives import serialization
    prefix = os.environ.get("ALPR_SNOWFLAKE_SSM_PREFIX")
    parameter = (prefix + "/private-key") if prefix else os.environ.get("ALPR_SNOWFLAKE_KEY_SSM_PARAM")
    if parameter:
        pem = _ssm_client().get_parameter(Name=parameter, WithDecryption=True)["Parameter"]["Value"].encode()
    else:
        with open(os.environ["ALPR_SNOWFLAKE_KEY_PATH"], "rb") as f:
            pem = f.read()
    return serialization.load_pem_private_key(pem, password=None).private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def account_and_user():
    """Account and user come from env, or from project SSM parameters (never committed)."""
    prefix = os.environ.get("ALPR_SNOWFLAKE_SSM_PREFIX")
    account, user = os.environ.get("ALPR_SNOWFLAKE_ACCOUNT"), os.environ.get("ALPR_SNOWFLAKE_USER")
    if prefix and not (account and user):
        ssm = _ssm_client()
        account = ssm.get_parameter(Name=prefix + "/account", WithDecryption=True)["Parameter"]["Value"]
        user = ssm.get_parameter(Name=prefix + "/user", WithDecryption=True)["Parameter"]["Value"]
    if not account or not user:
        raise ViewUnavailable("ALPR_LIVE_VIEW_FAILED:ConfigMissing")
    return account, user


def live_connection():
    connection = _live.get("connection")
    if connection is not None and not connection.is_closed():
        return connection
    import snowflake.connector
    account, user = account_and_user()
    connection = snowflake.connector.connect(
        account=account, user=user,
        private_key=private_key_der(), role=ROLE, warehouse=WAREHOUSE,
        login_timeout=live_timeout(LOGIN_TIMEOUT), network_timeout=live_timeout(NETWORK_TIMEOUT),
        session_parameters={"STATEMENT_TIMEOUT_IN_SECONDS": live_timeout(STATEMENT_TIMEOUT)})
    _live["connection"] = isolate(connection)
    return connection


def isolate(connection):
    cursor = connection.cursor()
    # The user's DEFAULT_SECONDARY_ROLES would otherwise add every other granted role's privileges.
    bounds = {"timeout": live_timeout(STATEMENT_TIMEOUT)} if _live_access.get() else {}
    cursor.execute("USE SECONDARY ROLES NONE", **bounds)
    cursor.execute("SELECT CURRENT_ROLE(), CURRENT_SECONDARY_ROLES()", **bounds)
    role, secondary = cursor.fetchone()
    if role != ROLE or json.loads(secondary).get("roles"):
        connection.close()
        raise ViewUnavailable("ALPR_READONLY_SESSION_NOT_ISOLATED")
    return connection


def live_rows(query_id: str, case_id: str) -> list:
    view, order = VIEWS[query_id]
    cursor = live_connection().cursor()
    bounds = {"timeout": live_timeout(STATEMENT_TIMEOUT)} if _live_access.get() else {}
    cursor.execute(f"SELECT * FROM {view} WHERE CASE_ID = %s ORDER BY {order} LIMIT {ROW_LIMIT}", (case_id,), **bounds)
    # Real Snowflake query id (cursor.sfqid): joins the Studio run trace to Snowflake QUERY_HISTORY.
    _query_ids.set((*_query_ids.get(), {
        "query_id": query_id, "case_id": case_id, "view": view,
        "snowflake_query_id": getattr(cursor, "sfqid", None)}))
    columns = [c[0] for c in cursor.description]
    return [{k: normalize(v) for k, v in zip(columns, row)} for row in cursor.fetchall()]


def consume_query_ids() -> list:
    """Drain only this context's Snowflake query ids (evidence, not data)."""
    query_ids = _query_ids.get()
    _query_ids.set(())
    return list(query_ids)


def rows(query_id: str, case_id: str) -> list:
    if query_id not in VIEWS or not isinstance(case_id, str) or not CASE_ID.match(case_id):
        raise ViewUnavailable("ALPR_QUERY_NOT_WHITELISTED")
    source = os.getenv("ALPR_VIEW_SOURCE", "snapshot")
    access = _live_access.get()
    if access:
        if source != "live":
            raise ViewUnavailable("ALPR_LIVE_CONFIG_REQUIRED")
        live_timeout(STATEMENT_TIMEOUT)
        access[0](VIEWS[query_id][0])
    if source == "snapshot":
        return [dict(r) for r in json.loads(SNAPSHOT.read_text())["rows"][query_id] if r["CASE_ID"] == case_id]
    if source != "live":
        raise ViewUnavailable("ALPR_VIEW_SOURCE_INVALID")
    try:
        result = live_rows(query_id, case_id)
        if access:
            live_timeout(STATEMENT_TIMEOUT)
        return result
    except ViewUnavailable:
        raise
    except Exception as exc:
        # Driver messages can carry account or user identifiers; report the type only.
        raise ViewUnavailable(f"ALPR_LIVE_VIEW_FAILED:{type(exc).__name__}") from None


def money(value) -> decimal.Decimal:
    return decimal.Decimal(str(value or "0"))


def ownership_review(case_id: str, ownership: list) -> dict:
    if not ownership:
        return {"case_id": case_id, "ownership_status": "CASE_NOT_FOUND", "event_time_owner_accounts": [], "steps": [],
                "answer": f"No ownership evidence for {case_id}."}
    first = ownership[0]
    corrected = [r for r in ownership if r["PLATE_ROLE"] == "CORRECTED"]
    owners = sorted({r["ACCOUNT_ID"] for r in corrected if r["ACCOUNT_ID"]})
    status = "MISSING" if not owners else "CONFLICTING" if len(owners) > 1 else "RESOLVED"
    records = ", ".join(f"{r['ACCOUNT_ID']} ({r['RECORD_SOURCE']})" for r in corrected if r["ACCOUNT_ID"]) or "none"
    conclusion = {"RESOLVED": f"Event-time owner is {owners[0] if owners else ''}.",
                  "MISSING": "No ownership record covers the passage time: manual review required.",
                  "CONFLICTING": f"{len(owners)} overlapping ownership records at the passage time: manual review required."}[status]
    steps = [
        {"step": "read_plates", "finding": f"Passage {first['EVENT_ID']} at {first['PLAZA_ID']} {first['EVENT_TS']}: raw read {first['RAW_PLATE_READ']}, "
                                           f"corrected {first['CORRECTED_PLATE']}, OCR confidence {first['OCR_CONFIDENCE']}, misread={first['WAS_MISREAD']}."},
        {"step": "resolve_owner_at_event", "finding": f"Corrected plate {first['CORRECTED_PLATE']} -> vehicle {corrected[0]['VEHICLE_ID']}; "
                                                      f"ownership valid at the passage time: {records}."},
        {"step": "conclude", "finding": conclusion},
    ]
    return {"case_id": case_id, "ownership_status": status, "was_misread": first["WAS_MISREAD"], "raw_plate_read": first["RAW_PLATE_READ"],
            "corrected_plate": first["CORRECTED_PLATE"], "event_time_owner_accounts": owners,
            "raw_read_owner_accounts": sorted({r["ACCOUNT_ID"] for r in ownership if r["PLATE_ROLE"] == "RAW_READ" and r["ACCOUNT_ID"]}),
            "steps": steps, "answer": f"Ownership at event for {case_id}: {status}. {conclusion}"}


def billing_review(case_id: str, charges: list) -> dict:
    if not charges:
        return {"case_id": case_id, "billing_status": "CASE_NOT_FOUND", "wrong_party_charges": [], "correct_party_charges": [],
                "steps": [], "answer": f"No charges found for {case_id}."}
    wrong, correct = [], []
    for c in charges:
        closed = c["CHARGE_STATUS"] in ("WRITTEN_OFF", "VOID")
        due = ZERO if closed else max(money(c["LATEST_NOTICE_AMOUNT"]) - money(c["PAID_AMOUNT"]), ZERO)
        entry = {"charge_id": c["CHARGE_ID"], "account_id": c["BILLED_ACCOUNT_ID"], "billed_plate": c["BILLED_PLATE"], "status": c["CHARGE_STATUS"],
                 "paid": str(money(c["PAID_AMOUNT"])), "latest_notice": c["LATEST_NOTICE_TYPE"], "outstanding": str(due)}
        # Owner flag None means the event-time owner is missing or ambiguous: neither side is proven.
        if not c["BILLED_PLATE_MATCHES_CORRECTED"] or c["BILLED_ACCOUNT_IS_EVENT_TIME_OWNER"] is False:
            wrong.append(entry)
        elif c["BILLED_ACCOUNT_IS_EVENT_TIME_OWNER"]:
            correct.append(entry)
    status = "DOUBLE_BILLED" if wrong and correct else "WRONG_PARTY_BILLED" if wrong else "CORRECTLY_BILLED" if correct else "OWNER_UNRESOLVED"

    def describe(group):
        return "; ".join(f"{e['charge_id']} {e['account_id']} {e['status']} paid {e['paid']} outstanding {e['outstanding']}" for e in group) or "none"
    steps = [{"step": "list_charges", "finding": f"{len(charges)} charge(s) for passage {charges[0]['EVENT_ID']}."},
             {"step": "classify_parties", "finding": f"Wrong party: {describe(wrong)}. Event-time owner: {describe(correct)}."},
             {"step": "conclude", "finding": f"Billing status {status}."}]
    return {"case_id": case_id, "billing_status": status, "wrong_party_charges": wrong, "correct_party_charges": correct,
            "steps": steps, "answer": f"Billing review for {case_id}: {status}. {steps[1]['finding']}"}


def remediation_review(case_id: str, ownership: list, charges: list, remediations: list, policy: list) -> dict:
    own, bill = ownership_review(case_id, ownership), billing_review(case_id, charges)
    done = {}
    for r in remediations:
        if r["ACTION"] and r["ACTION_STATUS"] == "COMPLETED":
            done.setdefault(r["CHARGE_ID"], []).append((r["ACTION"], money(r["AMOUNT"]), r["REMEDIATION_ID"]))
    pending = []
    for charge in bill["wrong_party_charges"]:
        refunded = sum((amount for action, amount, _ in done.get(charge["charge_id"], []) if action == "REFUND"), ZERO)
        refund = max(money(charge["paid"]) - refunded, ZERO)
        void = ZERO if charge["charge_id"] in done else money(charge["outstanding"])
        if refund or void:
            pending.append({"charge_id": charge["charge_id"], "account_id": charge["account_id"], "refund_review": refund, "void_review": void})
    amount = ZERO
    if own["ownership_status"] != "RESOLVED":
        outcome = {"MISSING": "MANUAL_REVIEW_MISSING_OWNERSHIP", "CONFLICTING": "MANUAL_REVIEW_CONFLICTING_OWNERSHIP"}.get(own["ownership_status"], "CASE_NOT_FOUND")
        recommended = ["MANUAL_OWNERSHIP_REVIEW"] if outcome != "CASE_NOT_FOUND" else []
    elif not bill["wrong_party_charges"]:
        outcome, recommended = "NO_BILLING_ERROR", ["NO_ACTION"]
    elif not pending:
        outcome, recommended = "ALREADY_REMEDIATED", ["NO_FURTHER_ACTION_DO_NOT_DUPLICATE"]
    else:
        remedy = "REFUND" if any(p["refund_review"] for p in pending) else "VOID"
        outcome = (f"DOUBLE_BILLED_{remedy}_REVIEW" if bill["billing_status"] == "DOUBLE_BILLED"
                   else f"WRONG_PARTY_{remedy}_AND_REBILL_REVIEW")
        recommended = [f"{'REFUND' if p['refund_review'] else 'VOID'}_REVIEW {p['charge_id']} {p['account_id']}" for p in pending]
        if bill["billing_status"] == "WRONG_PARTY_BILLED":
            recommended.append(f"REBILL_REVIEW {own['event_time_owner_accounts'][0]}")
        amount = sum((p["refund_review"] + p["void_review"] for p in pending), ZERO)
    fee = next((p["RULE_VALUE"] for p in policy if p["RULE_KEY"] == "VIOLATION_FEE_USD"), "n/a")
    cited = sorted({f"{p['POLICY_ID']}:{p['VERSION']}" for p in policy})
    demo = bool(policy) and all(p["IS_DEMO_ASSUMPTION"] for p in policy)
    history = "; ".join(f"{action} {value} ({rid}) on {cid}" for cid, items in done.items() for action, value, rid in items) or "none"
    steps = [{"step": "ownership", "finding": own["answer"]},
             {"step": "billing", "finding": bill["answer"]},
             {"step": "remediation_history", "finding": f"Completed remediations: {history}."},
             {"step": "apply_policy", "finding": f"Policy in effect at the passage time: {', '.join(cited) or 'none'}; violation fee {fee}"
                                                 f"{'. All rules are DEMO ASSUMPTIONS, not customer-confirmed' if demo else ''}."},
             {"step": "recommend", "finding": f"{outcome}: {', '.join(recommended) or 'none'}; financial review amount {amount}. "
                                              "Recommendation for human review only; nothing was executed."}]
    return {"case_id": case_id, "outcome": outcome, "wrong_party_accounts": sorted({c["account_id"] for c in bill["wrong_party_charges"]}),
            "correct_party_account": own["event_time_owner_accounts"][0] if own["ownership_status"] == "RESOLVED" else None,
            "financial_review_amount": str(amount), "recommended_actions": recommended, "policy_versions": cited,
            "policy_is_demo_assumption": demo, "steps": steps, "answer": f"ALPR investigation {case_id}: {steps[-1]['finding']}"}


def review(operation: str, case_id: str, found: dict) -> dict:
    if operation == "alpr_ownership_review":
        return ownership_review(case_id, found["ownership_at_event"])
    if operation == "alpr_billing_review":
        return billing_review(case_id, found["charges_notices_payments"])
    return remediation_review(case_id, found["ownership_at_event"], found["charges_notices_payments"],
                              found["remediation_status"], found["policy_at_event"])


def capture_snapshot(case_ids):
    """Operator step: re-capture the snapshot from the LIVE views through the read-only path."""
    captured = {q: [r for c in case_ids for r in live_rows(q, c)] for q in VIEWS}
    SNAPSHOT.write_text(json.dumps({"provenance": {
        "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "role": ROLE, "secondary_roles": "NONE", "views": {q: v for q, (v, _) in VIEWS.items()},
        "note": "Verbatim rows from the live secure views. SYNTHETIC data; billing policy rules are demo assumptions."},
        "rows": captured}, indent=1) + "\n")
    return {q: len(r) for q, r in captured.items()}


if __name__ == "__main__":
    print(json.dumps(capture_snapshot([f"ALPR-C{i:03d}" for i in range(1, 9)])))
