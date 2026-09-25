"""Run the six ALPR cases through the REAL Snowflake SQL API transport.

Uses backend.alpr_live.build_reader (live mode, explicit opt-in) — never the
bootstrap discovery helper, never direct SQL. Each case gets a fresh reader.
Reports are compared against the local expected.json (gold lives eval-side
only; the reader cannot see the gold table). The private key is used in
memory only; never copied or printed.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from backend.alpr_live import build_reader  # noqa: E402
from backend.alpr_specialists import investigate_case  # noqa: E402

CONFIG = {
    "mode": "live",
    "live_confirmed": True,
    "account_url": "https://tcljaka-hr19243.snowflakecomputing.com",
    "user": "GAB_BOOTSTRAP",  # granted GAB_QUERY_READONLY; bootstrap ROLE never used
    "role": "GAB_QUERY_READONLY",
    "warehouse": "GAB_QUERY_WH",
    "private_key_path": str(pathlib.Path.home()
                            / ".local/share/gab-snowflake-bootstrap/rsa_key.p8"),
}
EXPECTED = json.loads(pathlib.Path(
    "/home/ec2-user/work/gab-alpr-delivery/expected.json").read_text())
GOLD = {row[0]: {"finding": row[2], "recommendation": row[3]} for row in EXPECTED}

results = {}
for i in range(1, 7):
    case_id = f"CASE-{i:03}"
    reader = build_reader(CONFIG, max_requests=24, max_seconds=180.0)
    report = investigate_case(reader, case_id)
    gold = GOLD[case_id]
    match = {
        "finding": report["finding"] == gold["finding"],
        "recommendation": report["recommendation"] == gold["recommendation"],
    }
    results[case_id] = {
        "live": report.get("live"),
        "synthetic": report.get("synthetic"),
        "finding": report["finding"],
        "recommendation": report["recommendation"],
        "expected_finding": gold["finding"],
        "expected_recommendation": gold["recommendation"],
        "match": match,
        "passed": all(match.values()),
        "evidence_event_ids": report.get("evidence", {}).get("event_ids"),
        "policy_version": report.get("policy_version"),
    }
    print(json.dumps({case_id: {"passed": results[case_id]["passed"],
                                "live": report.get("live"),
                                "match": match}}))

passed = sum(1 for r in results.values() if r["passed"])
summary = {"passed": passed, "total": 6,
           "all_live": all(r["live"] is True for r in results.values())}
out = pathlib.Path("/tmp/alpr_live_six_cases.json")
out.write_text(json.dumps({"summary": summary, "cases": results}, indent=2))
print(json.dumps(summary))
sys.exit(0 if passed == 6 and summary["all_live"] else 1)
