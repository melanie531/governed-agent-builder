#!/usr/bin/env python3
"""Provision the SYNTHETIC ALPR misread-plate investigation domain in Snowflake.

Operator-run bootstrap, NOT part of the app path. Creates (idempotently) two NEW schemas in
GAB_DEMO_DB and touches nothing else (the TPCH APPROVED views and the earlier ALPR_DEMO /
ALPR_APPROVED schemas are left as they are):
  ALPR_INVESTIGATION           base tables (synthetic rows, expected outcome per case)
  ALPR_INVESTIGATION_APPROVED  secure read-only specialist views (no expected outcomes)
and grants GAB_QUERY_READONLY USAGE on the approved schema + SELECT on those views only.

Every row is synthetic. Plates, accounts, owners, amounts (USD 4.75 toll, violation fees) and every
billing_policy rule are DEMO ASSUMPTIONS, not customer-confirmed. Nothing here issues refunds or
notices: remediation_history rows are recorded synthetic history only.

Key-pair auth; the key is read from SNOWFLAKE_BOOTSTRAP_KEY_PATH and never printed:
  SNOWFLAKE_BOOTSTRAP_KEY_PATH=... uv run --with snowflake-connector-python --with cryptography \\
    python scripts/alpr_snowflake_provision.py
"""
import json
import os

DB = "GAB_DEMO_DB"
RAW = f"{DB}.ALPR_INVESTIGATION"
APPROVED = f"{DB}.ALPR_INVESTIGATION_APPROVED"
READONLY_ROLE = "GAB_QUERY_READONLY"
WAREHOUSE = "GAB_QUERY_WH"

TABLES = {
    "INVESTIGATION_CASES": ("CASE_ID VARCHAR PRIMARY KEY, EVENT_ID VARCHAR, OPENED_AT TIMESTAMP_NTZ, STATUS VARCHAR, "
                            "COMPLAINANT_ACCOUNT_ID VARCHAR, ALLEGED_ISSUE VARCHAR, EXPECTED_OUTCOME_CODE VARCHAR, "
                            "EXPECTED_WRONG_PARTY_ACCOUNT_ID VARCHAR, EXPECTED_CORRECT_PARTY_ACCOUNT_ID VARCHAR, "
                            "EXPECTED_FINANCIAL_REVIEW_AMOUNT NUMBER(10,2), EXPECTED_OUTCOME_NOTES VARCHAR, IS_SYNTHETIC BOOLEAN"),
    "PASSAGE_EVENTS": "EVENT_ID VARCHAR PRIMARY KEY, PLAZA_ID VARCHAR, GANTRY_ID VARCHAR, EVENT_TS TIMESTAMP_NTZ, LANE NUMBER, DIRECTION VARCHAR",
    "PLATE_READS": ("READ_ID VARCHAR PRIMARY KEY, EVENT_ID VARCHAR, RAW_PLATE_READ VARCHAR, CORRECTED_PLATE VARCHAR, "
                    "OCR_CONFIDENCE NUMBER(4,2), WAS_MISREAD BOOLEAN, CORRECTED_BY VARCHAR, CORRECTED_AT TIMESTAMP_NTZ"),
    "VEHICLE_REGISTRATIONS": ("REGISTRATION_ID VARCHAR PRIMARY KEY, PLATE VARCHAR, VEHICLE_ID VARCHAR, MAKE VARCHAR, MODEL VARCHAR, "
                              "VALID_FROM TIMESTAMP_NTZ, VALID_TO TIMESTAMP_NTZ"),
    "ACCOUNT_OWNERSHIP": ("OWNERSHIP_ID VARCHAR PRIMARY KEY, VEHICLE_ID VARCHAR, ACCOUNT_ID VARCHAR, OWNER_NAME VARCHAR, "
                          "VALID_FROM TIMESTAMP_NTZ, VALID_TO TIMESTAMP_NTZ, RECORD_SOURCE VARCHAR"),
    "TOLL_CHARGES": ("CHARGE_ID VARCHAR PRIMARY KEY, EVENT_ID VARCHAR, BILLED_PLATE VARCHAR, BILLED_ACCOUNT_ID VARCHAR, "
                     "AMOUNT NUMBER(10,2), CHARGED_AT TIMESTAMP_NTZ, STATUS VARCHAR"),
    "NOTICES": "NOTICE_ID VARCHAR PRIMARY KEY, CHARGE_ID VARCHAR, ACCOUNT_ID VARCHAR, NOTICE_TYPE VARCHAR, ISSUED_AT TIMESTAMP_NTZ, AMOUNT NUMBER(10,2)",
    "PAYMENTS": "PAYMENT_ID VARCHAR PRIMARY KEY, CHARGE_ID VARCHAR, ACCOUNT_ID VARCHAR, AMOUNT NUMBER(10,2), PAID_AT TIMESTAMP_NTZ, METHOD VARCHAR",
    "REMEDIATION_HISTORY": ("REMEDIATION_ID VARCHAR PRIMARY KEY, CASE_ID VARCHAR, CHARGE_ID VARCHAR, ACCOUNT_ID VARCHAR, ACTION VARCHAR, "
                            "AMOUNT NUMBER(10,2), ACTIONED_AT TIMESTAMP_NTZ, ACTION_STATUS VARCHAR, NOTE VARCHAR"),
    "BILLING_POLICY": ("POLICY_ID VARCHAR, VERSION VARCHAR, RULE_KEY VARCHAR, RULE_VALUE VARCHAR, RULE_TEXT VARCHAR, "
                       "EFFECTIVE_FROM TIMESTAMP_NTZ, EFFECTIVE_TO TIMESTAMP_NTZ, IS_DEMO_ASSUMPTION BOOLEAN, NOTE VARCHAR"),
}

# (case, event, opened, complainant, alleged issue, expected code, wrong party, correct party, review amount, notes)
CASES = [
    ("ALPR-C001", "EV-50001", "2026-08-14 09:00", "ACCT-1002", "Billed for a trip my vehicle never made",
     "DOUBLE_BILLED_REFUND_REVIEW", "ACCT-1002", "ACCT-1001", 4.75,
     "Raw read ZTX4881 (B read as 8) billed ACCT-1002, who paid; corrected plate ZTX4B81 was also billed to its "
     "event-time owner ACCT-1001, who paid. Both parties billed for one passage: recommend refund review of 4.75 to "
     "ACCT-1002 and reversal of CHG-0001; the correct charge CHG-0002 stands."),
    ("ALPR-C002", "EV-50002", "2026-08-18 10:30", "ACCT-1004", "Violation notice for a toll I did not incur",
     "WRONG_PARTY_VOID_AND_REBILL_REVIEW", "ACCT-1004", "ACCT-1003", 29.75,
     "Raw read ZKM7015 (Q read as 0) billed ACCT-1004 only; unpaid, escalated to a violation notice of 29.75 "
     "(4.75 toll + 25.00 fee, policy v2). Correct party ACCT-1003 never billed: recommend voiding the charge and "
     "its notices (29.75 outstanding) and rebilling 4.75 to ACCT-1003."),
    ("ALPR-C003", "EV-50003", "2026-08-02 08:15", "ACCT-1006", "Charged twice for one misread trip",
     "ALREADY_REMEDIATED", "ACCT-1006", "ACCT-1005", 0.00,
     "Double billed like C001, but the 4.75 wrong-party payment by ACCT-1006 was already refunded (REM-0001). "
     "No further financial action; a second refund would duplicate the remediation."),
    ("ALPR-C004", "EV-50004", "2026-07-01 11:00", "ACCT-1008", "Invoice for a vehicle I do not own",
     "ALREADY_REMEDIATED", "ACCT-1008", "ACCT-1007", 0.00,
     "Raw read ZRD1177 (I read as 1) billed ACCT-1008 (unpaid); charge already written off (REM-0002) and the "
     "correct owner ACCT-1007 was rebilled and paid. Event falls under policy v1. No further action."),
    ("ALPR-C005", "EV-50005", "2026-08-05 14:20", "ACCT-1010", "Toll billed to the wrong vehicle",
     "MANUAL_REVIEW_MISSING_OWNERSHIP", "ACCT-1010", None, 0.00,
     "Raw read ZHN3B09 billed ACCT-1010, but the corrected plate ZHN3809 (VEH-2009) has NO account ownership record "
     "at the event time (gap 2026-07-01 to 2026-08-15). Manual review; hold collection on CHG-0006; no rebill."),
    ("ALPR-C006", "EV-50006", "2026-08-09 16:45", "ACCT-1013", "Paid a toll for someone else's car",
     "MANUAL_REVIEW_CONFLICTING_OWNERSHIP", "ACCT-1013", None, 0.00,
     "Raw read ZWV6644 billed ACCT-1013, who paid; the corrected plate ZWV6G44 (VEH-2011) has TWO overlapping "
     "ownership records at event time (ACCT-1011 DMV feed, ACCT-1012 customer portal). Manual review before any "
     "refund or rebill."),
    ("ALPR-C007", "EV-50007", "2026-07-25 13:10", "ACCT-1015", "Billed for a trip before I bought the car",
     "WRONG_PARTY_REFUND_AND_REBILL_REVIEW", "ACCT-1015", "ACCT-1014", 4.75,
     "Plate read correctly (no misread). Event 2026-07-05 predates the 2026-07-10 ownership transfer, but the charge "
     "went to the current owner ACCT-1015, who paid. Recommend refund review of 4.75 to ACCT-1015 and rebilling the "
     "event-time owner ACCT-1014."),
    ("ALPR-C008", "EV-50008", "2026-08-21 09:40", "ACCT-1016", "Believes the camera misread my plate",
     "NO_BILLING_ERROR", None, "ACCT-1016", 0.00,
     "High-confidence read (0.98), not misread; ACCT-1016 owned the vehicle at event time and was billed once. "
     "No billing error: complaint not substantiated, no action."),
]
BACKGROUND_PLATES = ["ZAA1017", "ZAB1018", "ZAC1019", "ZAD1020"]
EVENTS = [
    ("EV-50001", "PLZ-NORTH-01", "G-N01-A", "2026-08-03 07:42:10", 2, "NB"),
    ("EV-50002", "PLZ-EAST-02", "G-E02-B", "2026-07-14 18:05:44", 1, "EB"),
    ("EV-50003", "PLZ-NORTH-01", "G-N01-B", "2026-07-22 06:58:31", 3, "SB"),
    ("EV-50004", "PLZ-WEST-03", "G-W03-A", "2026-06-20 12:30:02", 2, "WB"),
    ("EV-50005", "PLZ-EAST-02", "G-E02-A", "2026-07-28 08:14:55", 2, "EB"),
    ("EV-50006", "PLZ-SOUTH-04", "G-S04-A", "2026-07-30 17:22:18", 1, "NB"),
    ("EV-50007", "PLZ-WEST-03", "G-W03-B", "2026-07-05 09:11:40", 1, "EB"),
    ("EV-50008", "PLZ-NORTH-01", "G-N01-A", "2026-08-11 07:55:03", 2, "NB"),
] + [(f"EV-5{100 + i}", ["PLZ-NORTH-01", "PLZ-EAST-02", "PLZ-WEST-03", "PLZ-SOUTH-04"][i % 4], f"G-BG-{i % 3}",
      f"2026-0{6 + i % 3}-{10 + i:02d} 0{7 + i % 3}:{10 + i}:00", 1 + i % 3, ["NB", "SB", "EB", "WB"][i % 4]) for i in range(12)]
READS = [
    ("RD-50001", "EV-50001", "ZTX4881", "ZTX4B81", 0.62, True, "SYN-REVIEWER-07", "2026-08-05 10:00"),
    ("RD-50002", "EV-50002", "ZKM7015", "ZKM7Q15", 0.71, True, "SYN-REVIEWER-03", "2026-08-19 09:00"),
    ("RD-50003", "EV-50003", "ZPL5S20", "ZPL5520", 0.58, True, "SYN-REVIEWER-07", "2026-07-24 15:00"),
    ("RD-50004", "EV-50004", "ZRD1177", "ZRDI177", 0.66, True, "SYN-REVIEWER-01", "2026-06-25 11:00"),
    ("RD-50005", "EV-50005", "ZHN3B09", "ZHN3809", 0.64, True, "SYN-REVIEWER-03", "2026-08-06 13:00"),
    ("RD-50006", "EV-50006", "ZWV6644", "ZWV6G44", 0.69, True, "SYN-REVIEWER-01", "2026-08-10 10:00"),
    ("RD-50007", "EV-50007", "ZBQ2210", "ZBQ2210", 0.97, False, None, None),
    ("RD-50008", "EV-50008", "ZCE9031", "ZCE9031", 0.98, False, None, None),
] + [(f"RD-5{100 + i}", f"EV-5{100 + i}", BACKGROUND_PLATES[i % 4], BACKGROUND_PLATES[i % 4], 0.95, False, None, None) for i in range(12)]
# (plate, vehicle, make, model); every registration valid 2025-01-01 onward.
VEHICLES = [
    ("ZTX4B81", "VEH-2001", "Toyota", "Corolla"), ("ZTX4881", "VEH-2002", "Honda", "Civic"),
    ("ZKM7Q15", "VEH-2003", "Ford", "F-150"), ("ZKM7015", "VEH-2004", "Subaru", "Outback"),
    ("ZPL5520", "VEH-2005", "Tesla", "Model 3"), ("ZPL5S20", "VEH-2006", "Kia", "Sorento"),
    ("ZRDI177", "VEH-2007", "Mazda", "CX-5"), ("ZRD1177", "VEH-2008", "Nissan", "Leaf"),
    ("ZHN3809", "VEH-2009", "Hyundai", "Tucson"), ("ZHN3B09", "VEH-2010", "Chevrolet", "Malibu"),
    ("ZWV6G44", "VEH-2011", "Volkswagen", "Golf"), ("ZWV6644", "VEH-2013", "BMW", "X3"),
    ("ZBQ2210", "VEH-2014", "Jeep", "Wrangler"), ("ZCE9031", "VEH-2016", "Audi", "A4"),
    ("ZAA1017", "VEH-2017", "Toyota", "RAV4"), ("ZAB1018", "VEH-2018", "Honda", "CR-V"),
    ("ZAC1019", "VEH-2019", "Ford", "Escape"), ("ZAD1020", "VEH-2020", "Kia", "Soul"),
]
OPEN = None
# (ownership, vehicle, account, valid_from, valid_to, source); half-open [from, to) intervals.
OWNERSHIP = [
    ("OWN-3001", "VEH-2001", "ACCT-1001", "2025-01-01", OPEN, "DMV_FEED"),
    ("OWN-3002", "VEH-2002", "ACCT-1002", "2025-01-01", OPEN, "DMV_FEED"),
    ("OWN-3003", "VEH-2003", "ACCT-1003", "2025-01-01", OPEN, "DMV_FEED"),
    ("OWN-3004", "VEH-2004", "ACCT-1004", "2025-01-01", OPEN, "DMV_FEED"),
    ("OWN-3005", "VEH-2005", "ACCT-1005", "2025-01-01", OPEN, "DMV_FEED"),
    ("OWN-3006", "VEH-2006", "ACCT-1006", "2025-01-01", OPEN, "DMV_FEED"),
    ("OWN-3007", "VEH-2007", "ACCT-1007", "2025-01-01", OPEN, "DMV_FEED"),
    ("OWN-3008", "VEH-2008", "ACCT-1008", "2025-01-01", OPEN, "DMV_FEED"),
    ("OWN-3009", "VEH-2009", "ACCT-1009", "2025-01-01", "2026-07-01", "DMV_FEED"),   # gap: nobody 07-01..08-15
    ("OWN-3010", "VEH-2009", "ACCT-1021", "2026-08-15", OPEN, "DMV_FEED"),
    ("OWN-3011", "VEH-2010", "ACCT-1010", "2025-01-01", OPEN, "DMV_FEED"),
    ("OWN-3012", "VEH-2011", "ACCT-1011", "2025-01-01", OPEN, "DMV_FEED"),            # conflicts with OWN-3013
    ("OWN-3013", "VEH-2011", "ACCT-1012", "2026-06-01", OPEN, "CUSTOMER_PORTAL"),
    ("OWN-3014", "VEH-2013", "ACCT-1013", "2025-01-01", OPEN, "DMV_FEED"),
    ("OWN-3015", "VEH-2014", "ACCT-1014", "2025-01-01", "2026-07-10", "DMV_FEED"),   # sold after the event
    ("OWN-3016", "VEH-2014", "ACCT-1015", "2026-07-10", OPEN, "DMV_FEED"),
    ("OWN-3017", "VEH-2016", "ACCT-1016", "2025-01-01", OPEN, "DMV_FEED"),
] + [(f"OWN-30{18 + i}", f"VEH-20{17 + i}", f"ACCT-10{17 + i}", "2025-01-01", OPEN, "DMV_FEED") for i in range(4)]
CHARGES = [
    ("CHG-0001", "EV-50001", "ZTX4881", "ACCT-1002", 4.75, "2026-08-03 23:00", "POSTED"),
    ("CHG-0002", "EV-50001", "ZTX4B81", "ACCT-1001", 4.75, "2026-08-05 23:00", "POSTED"),
    ("CHG-0003", "EV-50002", "ZKM7015", "ACCT-1004", 4.75, "2026-07-14 23:00", "ESCALATED"),
    ("CHG-0004", "EV-50003", "ZPL5S20", "ACCT-1006", 4.75, "2026-07-22 23:00", "POSTED"),
    ("CHG-0005", "EV-50003", "ZPL5520", "ACCT-1005", 4.75, "2026-07-24 23:00", "POSTED"),
    ("CHG-0006", "EV-50005", "ZHN3B09", "ACCT-1010", 4.75, "2026-07-28 23:00", "POSTED"),
    ("CHG-0007", "EV-50006", "ZWV6644", "ACCT-1013", 4.75, "2026-07-30 23:00", "POSTED"),
    ("CHG-0008", "EV-50007", "ZBQ2210", "ACCT-1015", 4.75, "2026-07-20 23:00", "POSTED"),
    ("CHG-0009", "EV-50008", "ZCE9031", "ACCT-1016", 4.75, "2026-08-11 23:00", "POSTED"),
    ("CHG-0010", "EV-50004", "ZRD1177", "ACCT-1008", 4.75, "2026-06-20 23:00", "WRITTEN_OFF"),
    ("CHG-0011", "EV-50004", "ZRDI177", "ACCT-1007", 4.75, "2026-06-26 23:00", "POSTED"),
] + [(f"CHG-01{i:02d}", f"EV-5{100 + i}", BACKGROUND_PLATES[i % 4], f"ACCT-10{17 + i % 4}", 4.75,
      f"2026-0{6 + i % 3}-{10 + i:02d} 23:00", "POSTED") for i in range(12)]
NOTICES = [
    ("NTC-0001", "CHG-0001", "ACCT-1002", "INVOICE", "2026-08-04", 4.75),
    ("NTC-0002", "CHG-0002", "ACCT-1001", "INVOICE", "2026-08-06", 4.75),
    ("NTC-0003", "CHG-0003", "ACCT-1004", "INVOICE", "2026-07-15", 4.75),
    ("NTC-0004", "CHG-0003", "ACCT-1004", "VIOLATION_NOTICE", "2026-08-14", 29.75),
    ("NTC-0005", "CHG-0004", "ACCT-1006", "INVOICE", "2026-07-23", 4.75),
    ("NTC-0006", "CHG-0005", "ACCT-1005", "INVOICE", "2026-07-25", 4.75),
    ("NTC-0007", "CHG-0006", "ACCT-1010", "INVOICE", "2026-07-29", 4.75),
    ("NTC-0008", "CHG-0007", "ACCT-1013", "INVOICE", "2026-07-31", 4.75),
    ("NTC-0009", "CHG-0008", "ACCT-1015", "INVOICE", "2026-07-21", 4.75),
    ("NTC-0010", "CHG-0009", "ACCT-1016", "INVOICE", "2026-08-12", 4.75),
    ("NTC-0011", "CHG-0010", "ACCT-1008", "INVOICE", "2026-06-21", 4.75),
    ("NTC-0012", "CHG-0011", "ACCT-1007", "INVOICE", "2026-06-27", 4.75),
]
PAYMENTS = [
    ("PAY-0001", "CHG-0001", "ACCT-1002", 4.75, "2026-08-10", "CARD"),
    ("PAY-0002", "CHG-0002", "ACCT-1001", 4.75, "2026-08-12", "AUTOPAY"),
    ("PAY-0003", "CHG-0004", "ACCT-1006", 4.75, "2026-07-26", "CARD"),
    ("PAY-0004", "CHG-0005", "ACCT-1005", 4.75, "2026-07-28", "AUTOPAY"),
    ("PAY-0005", "CHG-0007", "ACCT-1013", 4.75, "2026-08-03", "CARD"),
    ("PAY-0006", "CHG-0008", "ACCT-1015", 4.75, "2026-07-24", "ACH"),
    ("PAY-0007", "CHG-0009", "ACCT-1016", 4.75, "2026-08-15", "AUTOPAY"),
    ("PAY-0008", "CHG-0011", "ACCT-1007", 4.75, "2026-07-02", "CARD"),
] + [(f"PAY-01{i:02d}", f"CHG-01{i:02d}", f"ACCT-10{17 + i % 4}", 4.75, f"2026-0{6 + i % 3}-{12 + i:02d}", "AUTOPAY") for i in range(12)]
REMEDIATIONS = [
    ("REM-0001", "ALPR-C003", "CHG-0004", "ACCT-1006", "REFUND", 4.75, "2026-08-06 12:00", "COMPLETED",
     "Synthetic history: wrong-party payment refunded before this investigation. Not executed by the platform."),
    ("REM-0002", "ALPR-C004", "CHG-0010", "ACCT-1008", "WRITE_OFF", 4.75, "2026-06-26 09:00", "COMPLETED",
     "Synthetic history: wrong-party charge written off; correct owner rebilled as CHG-0011. Not executed by the platform."),
]
DEMO = "DEMO ASSUMPTION: not confirmed by the customer."
POLICY = [
    ("BP-OCR-REVIEW", "v1", "OCR_MANUAL_REVIEW_THRESHOLD", "0.80", "Reads below this OCR confidence require human plate review before billing.", "2026-01-01", "2026-07-01", True, DEMO),
    ("BP-OCR-REVIEW", "v2", "OCR_MANUAL_REVIEW_THRESHOLD", "0.85", "Reads below this OCR confidence require human plate review before billing.", "2026-07-01", OPEN, True, DEMO),
    ("BP-VIOLATION-FEE", "v1", "VIOLATION_FEE_USD", "20.00", "Unpaid invoices escalate to a violation notice with this fee added.", "2026-01-01", "2026-07-01", True, DEMO),
    ("BP-VIOLATION-FEE", "v2", "VIOLATION_FEE_USD", "25.00", "Unpaid invoices escalate to a violation notice with this fee added.", "2026-07-01", OPEN, True, DEMO),
    ("BP-ESCALATION", "v1", "ESCALATION_DAYS", "30", "Days after the invoice before an unpaid charge escalates.", "2026-01-01", OPEN, True, DEMO),
    ("BP-OWNER-AT-EVENT", "v1", "LIABLE_PARTY", "EVENT_TIME_OWNER", "The liable account is the owner of the vehicle registered to the corrected plate at the passage time (half-open validity).", "2026-01-01", OPEN, True, DEMO),
    ("BP-WRONG-PARTY", "v1", "WRONG_PARTY_REMEDY", "REFUND_PAID_VOID_UNPAID", "Wrong-party charges: refund amounts paid, void unpaid charges and their notices (fees included). Never duplicate a completed refund or write-off.", "2026-01-01", OPEN, True, DEMO),
    ("BP-OWNER-UNRESOLVED", "v1", "UNRESOLVED_OWNER_REMEDY", "MANUAL_REVIEW", "Missing or conflicting event-time ownership: manual review; no refund, void or rebill until resolved.", "2026-01-01", OPEN, True, DEMO),
    ("BP-REBILL-WINDOW", "v1", "REBILL_WINDOW_DAYS", "90", "The correct party may be rebilled within this many days of the passage.", "2026-01-01", OPEN, True, DEMO),
]

ROWS = {
    "INVESTIGATION_CASES": [(c, e, o, "OPEN", comp, issue, code, wrong, right, amt, notes, True)
                            for c, e, o, comp, issue, code, wrong, right, amt, notes in CASES],
    "PASSAGE_EVENTS": EVENTS,
    "PLATE_READS": READS,
    "VEHICLE_REGISTRATIONS": [(f"REG-{v[4:]}", plate, v, make, model, "2025-01-01", OPEN) for plate, v, make, model in VEHICLES],
    "ACCOUNT_OWNERSHIP": [(o, v, a, f"Synthetic Owner {a[5:]}", f, t, s) for o, v, a, f, t, s in OWNERSHIP],
    "TOLL_CHARGES": CHARGES,
    "NOTICES": NOTICES,
    "PAYMENTS": PAYMENTS,
    "REMEDIATION_HISTORY": REMEDIATIONS,
    "BILLING_POLICY": POLICY,
}

# Case-scoped context shared by the specialist views (expected outcomes are never selected).
CASE_READ = f"""SELECT c.CASE_ID, e.EVENT_ID, e.PLAZA_ID, e.GANTRY_ID, e.EVENT_TS, e.LANE, e.DIRECTION,
    r.RAW_PLATE_READ, r.CORRECTED_PLATE, r.OCR_CONFIDENCE, r.WAS_MISREAD
  FROM {RAW}.INVESTIGATION_CASES c JOIN {RAW}.PASSAGE_EVENTS e ON e.EVENT_ID = c.EVENT_ID
  JOIN {RAW}.PLATE_READS r ON r.EVENT_ID = e.EVENT_ID"""
VIEWS = {
    # Account/Vehicle specialist: who owned each read plate's vehicle AT the passage time.
    "VW_OWNERSHIP_AT_EVENT": f"""WITH cr AS ({CASE_READ}),
  plates AS (SELECT cr.*, 'CORRECTED' AS PLATE_ROLE, cr.CORRECTED_PLATE AS PLATE FROM cr
             UNION ALL SELECT cr.*, 'RAW_READ', cr.RAW_PLATE_READ FROM cr WHERE cr.WAS_MISREAD)
SELECT p.CASE_ID, p.EVENT_ID, p.PLAZA_ID, p.GANTRY_ID, p.EVENT_TS, p.LANE, p.DIRECTION, p.RAW_PLATE_READ,
  p.CORRECTED_PLATE, p.OCR_CONFIDENCE, p.WAS_MISREAD, p.PLATE_ROLE, p.PLATE, v.VEHICLE_ID, v.MAKE, v.MODEL,
  o.OWNERSHIP_ID, o.ACCOUNT_ID, o.OWNER_NAME, o.VALID_FROM AS OWNERSHIP_VALID_FROM, o.VALID_TO AS OWNERSHIP_VALID_TO,
  o.RECORD_SOURCE, COUNT(o.OWNERSHIP_ID) OVER (PARTITION BY p.CASE_ID, p.PLATE_ROLE) AS OWNER_RECORDS_AT_EVENT
FROM plates p
LEFT JOIN {RAW}.VEHICLE_REGISTRATIONS v ON v.PLATE = p.PLATE
     AND p.EVENT_TS >= v.VALID_FROM AND (v.VALID_TO IS NULL OR p.EVENT_TS < v.VALID_TO)
LEFT JOIN {RAW}.ACCOUNT_OWNERSHIP o ON o.VEHICLE_ID = v.VEHICLE_ID
     AND p.EVENT_TS >= o.VALID_FROM AND (o.VALID_TO IS NULL OR p.EVENT_TS < o.VALID_TO)""",
    # Billing/Notice specialist: every charge for the case's passage with notices and payments. The
    # owner flag is NULL when the event-time owner of the corrected plate is missing or ambiguous.
    "VW_CHARGES_NOTICES_PAYMENTS": f"""WITH cr AS ({CASE_READ}),
  owners AS (SELECT cr.CASE_ID, o.ACCOUNT_ID FROM cr
    JOIN {RAW}.VEHICLE_REGISTRATIONS v ON v.PLATE = cr.CORRECTED_PLATE
         AND cr.EVENT_TS >= v.VALID_FROM AND (v.VALID_TO IS NULL OR cr.EVENT_TS < v.VALID_TO)
    JOIN {RAW}.ACCOUNT_OWNERSHIP o ON o.VEHICLE_ID = v.VEHICLE_ID
         AND cr.EVENT_TS >= o.VALID_FROM AND (o.VALID_TO IS NULL OR cr.EVENT_TS < o.VALID_TO)),
  owner_counts AS (SELECT cr.CASE_ID, COUNT(owners.ACCOUNT_ID) AS N FROM cr LEFT JOIN owners ON owners.CASE_ID = cr.CASE_ID GROUP BY cr.CASE_ID),
  notices AS (SELECT CHARGE_ID, COUNT(*) AS NOTICE_COUNT, MAX_BY(NOTICE_TYPE, ISSUED_AT) AS LATEST_NOTICE_TYPE,
                     MAX_BY(AMOUNT, ISSUED_AT) AS LATEST_NOTICE_AMOUNT FROM {RAW}.NOTICES GROUP BY CHARGE_ID),
  pays AS (SELECT CHARGE_ID, COUNT(*) AS PAYMENT_COUNT, SUM(AMOUNT) AS PAID_AMOUNT FROM {RAW}.PAYMENTS GROUP BY CHARGE_ID)
SELECT cr.CASE_ID, cr.EVENT_ID, cr.EVENT_TS, cr.CORRECTED_PLATE, t.CHARGE_ID, t.BILLED_PLATE, t.BILLED_ACCOUNT_ID,
  t.AMOUNT AS CHARGE_AMOUNT, t.CHARGED_AT, t.STATUS AS CHARGE_STATUS,
  t.BILLED_PLATE = cr.CORRECTED_PLATE AS BILLED_PLATE_MATCHES_CORRECTED,
  IFF(oc.N <> 1, NULL, bo.ACCOUNT_ID IS NOT NULL) AS BILLED_ACCOUNT_IS_EVENT_TIME_OWNER,
  COALESCE(n.NOTICE_COUNT, 0) AS NOTICE_COUNT, n.LATEST_NOTICE_TYPE, n.LATEST_NOTICE_AMOUNT,
  COALESCE(p.PAYMENT_COUNT, 0) AS PAYMENT_COUNT, COALESCE(p.PAID_AMOUNT, 0) AS PAID_AMOUNT
FROM cr JOIN owner_counts oc ON oc.CASE_ID = cr.CASE_ID
JOIN {RAW}.TOLL_CHARGES t ON t.EVENT_ID = cr.EVENT_ID
LEFT JOIN owners bo ON bo.CASE_ID = cr.CASE_ID AND bo.ACCOUNT_ID = t.BILLED_ACCOUNT_ID
LEFT JOIN notices n ON n.CHARGE_ID = t.CHARGE_ID LEFT JOIN pays p ON p.CHARGE_ID = t.CHARGE_ID""",
    # Remediation specialist: refunds / write-offs already recorded per charge of the case.
    "VW_REMEDIATION_STATUS": f"""SELECT c.CASE_ID, t.CHARGE_ID, t.BILLED_ACCOUNT_ID, t.STATUS AS CHARGE_STATUS, rh.REMEDIATION_ID,
  rh.ACTION, rh.AMOUNT, rh.ACTIONED_AT, rh.ACTION_STATUS, rh.NOTE
FROM {RAW}.INVESTIGATION_CASES c JOIN {RAW}.TOLL_CHARGES t ON t.EVENT_ID = c.EVENT_ID
LEFT JOIN {RAW}.REMEDIATION_HISTORY rh ON rh.CHARGE_ID = t.CHARGE_ID""",
    # Remediation specialist: billing policy version in effect at the passage time.
    "VW_POLICY_AT_EVENT": f"""SELECT c.CASE_ID, e.EVENT_TS, bp.POLICY_ID, bp.VERSION, bp.RULE_KEY, bp.RULE_VALUE, bp.RULE_TEXT,
  bp.EFFECTIVE_FROM, bp.EFFECTIVE_TO, bp.IS_DEMO_ASSUMPTION, bp.NOTE
FROM {RAW}.INVESTIGATION_CASES c JOIN {RAW}.PASSAGE_EVENTS e ON e.EVENT_ID = c.EVENT_ID
JOIN {RAW}.BILLING_POLICY bp ON e.EVENT_TS >= bp.EFFECTIVE_FROM AND (bp.EFFECTIVE_TO IS NULL OR e.EVENT_TS < bp.EFFECTIVE_TO)""",
}


def statements():
    yield f"CREATE SCHEMA IF NOT EXISTS {RAW} COMMENT = 'SYNTHETIC ALPR misread-plate investigation base data; demo assumptions; no real refunds or notices'"
    yield f"CREATE SCHEMA IF NOT EXISTS {APPROVED} COMMENT = 'Secure read-only ALPR specialist views; expected outcomes excluded'"
    for name, ddl in TABLES.items():
        yield f"CREATE OR REPLACE TABLE {RAW}.{name} ({ddl}) COMMENT = 'SYNTHETIC demo data'"
    for name, select in VIEWS.items():
        yield f"CREATE OR REPLACE SECURE VIEW {APPROVED}.{name} COMMENT = 'SYNTHETIC ALPR specialist view (read-only)' AS {select}"
    # Least privilege: the approved schema and its views only; never the base schema.
    yield f"GRANT USAGE ON DATABASE {DB} TO ROLE {READONLY_ROLE}"
    yield f"GRANT USAGE ON WAREHOUSE {WAREHOUSE} TO ROLE {READONLY_ROLE}"
    yield f"GRANT USAGE ON SCHEMA {APPROVED} TO ROLE {READONLY_ROLE}"
    for name in VIEWS:
        yield f"GRANT SELECT ON VIEW {APPROVED}.{name} TO ROLE {READONLY_ROLE}"


def connect():
    import snowflake.connector
    from cryptography.hazmat.primitives import serialization
    with open(os.environ["SNOWFLAKE_BOOTSTRAP_KEY_PATH"], "rb") as f:
        der = serialization.load_pem_private_key(f.read(), password=None).private_bytes(
            serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return snowflake.connector.connect(account=os.getenv("SNOWFLAKE_ACCOUNT", "TCLJAKA-HR19243"),
                                       user=os.getenv("SNOWFLAKE_USER", "GAB_BOOTSTRAP"), private_key=der,
                                       role="GAB_BOOTSTRAP_ROLE", warehouse=WAREHOUSE)


def main():
    connection = connect()
    try:
        cursor = connection.cursor()
        for sql in statements():
            cursor.execute(sql)
            if sql.startswith("CREATE OR REPLACE TABLE"):
                name = sql.split()[4].rsplit(".", 1)[1]
                rows = ROWS[name]
                cursor.executemany(f"INSERT INTO {RAW}.{name} VALUES ({', '.join(['%s'] * len(rows[0]))})", rows)
        counts = {}
        for schema, names in ((RAW, TABLES), (APPROVED, VIEWS)):
            for name in names:
                cursor.execute(f"SELECT COUNT(*) FROM {schema}.{name}")
                counts[name] = cursor.fetchone()[0]
    finally:
        connection.close()
    print(json.dumps({"status": "PROVISIONED", "raw_schema": RAW, "approved_schema": APPROVED, "row_counts": counts}, indent=1))


if __name__ == "__main__":
    main()
