"""Per-call Snowflake evidence isolation, using only offline synthetic cursors."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from backend import alpr, harness


CONNECTOR = "snowflake-alpr-views"


def call(case_id, tool=CONNECTOR):
    manifest = {"tools": [tool], "component_versions": {tool: "1"}}
    arguments = ({"query_id": "ownership_at_event", "case_id": case_id}
                 if tool == CONNECTOR else {"question": f"Investigate {case_id}"})
    return harness.call_tool(manifest, tool, arguments, harness.tool_scope(tool))


def evidence(result):
    return [(item["query_id"], item["case_id"], item["snowflake_query_id"])
            for item in result["snowflake_query_ids"]]


@pytest.fixture
def cursor_type(monkeypatch):
    class Cursor:
        description = [("CASE_ID",)]

        def execute(self, statement, params):
            self.case_id = params[0]
            self.sfqid = "synthetic-query-" + self.case_id

        def fetchall(self):
            return [(self.case_id,)]

    class Connection:
        def cursor(self):
            return Cursor()

    monkeypatch.setattr(alpr, "_live", {})
    monkeypatch.setattr(alpr, "live_connection", lambda: Connection())
    monkeypatch.setenv("ALPR_VIEW_SOURCE", "live")
    alpr.consume_query_ids()
    yield Cursor
    alpr.consume_query_ids()


@pytest.mark.parametrize("stage", ["execute", "description", "fetchall", "normalize"])
def test_failed_call_cleans_evidence_before_next_success(monkeypatch, cursor_type, stage):
    def fail(case_id):
        if case_id == "ALPR-C901":
            raise RuntimeError("synthetic fetch detail must not escape")

    if stage == "normalize":
        original = alpr.normalize

        def normalize(value):
            fail(value)
            return original(value)

        monkeypatch.setattr(alpr, "normalize", normalize)
    elif stage == "description":
        def description(cursor):
            fail(cursor.case_id)
            return [("CASE_ID",)]

        monkeypatch.setattr(cursor_type, "description", property(description))
    else:
        original = getattr(cursor_type, stage)

        def invoke(cursor, *args):
            result = original(cursor, *args)
            fail(cursor.case_id)
            return result

        monkeypatch.setattr(cursor_type, stage, invoke)

    with pytest.raises(harness.ToolDenied, match="^ALPR_LIVE_VIEW_FAILED:RuntimeError$"):
        call("ALPR-C901")
    # Check cleanup immediately: a reset only at the next call is insufficient.
    assert alpr.consume_query_ids() == []
    result = call("ALPR-C902")
    assert result["rows"] == [{"CASE_ID": "ALPR-C902"}]
    assert evidence(result) == [("ownership_at_event", "ALPR-C902", "synthetic-query-ALPR-C902")]
    assert alpr.consume_query_ids() == []


def test_snapshot_call_excludes_unconsumed_live_evidence(monkeypatch, cursor_type):
    alpr.rows("ownership_at_event", "ALPR-C901")
    monkeypatch.setenv("ALPR_VIEW_SOURCE", "snapshot")
    result = call("ALPR-C999")
    assert result["rows"] == [] and result["snowflake_query_ids"] == []
    # A call must not drain another caller's successful evidence either.
    assert [item["case_id"] for item in alpr.consume_query_ids()] == ["ALPR-C901"]


def test_nested_calls_keep_all_successful_same_call_queries(monkeypatch, cursor_type):
    original = alpr.rows
    nested = []

    def rows(query_id, case_id):
        found = original(query_id, case_id)
        if case_id == "ALPR-C901":
            nested.append(call("ALPR-C902"))
            original("charges_notices_payments", case_id)
        return found

    monkeypatch.setattr(alpr, "rows", rows)
    outer = call("ALPR-C901")
    assert evidence(nested[0]) == [("ownership_at_event", "ALPR-C902", "synthetic-query-ALPR-C902")]
    assert evidence(outer) == [
        ("ownership_at_event", "ALPR-C901", "synthetic-query-ALPR-C901"),
        ("charges_notices_payments", "ALPR-C901", "synthetic-query-ALPR-C901"),
    ]
    assert alpr.consume_query_ids() == []


@pytest.mark.parametrize("first_fails", [False, True])
def test_overlapping_calls_cannot_steal_or_mix_evidence(monkeypatch, cursor_type, first_fails):
    fetching, release = Event(), Event()

    def fetchall(cursor):
        if cursor.case_id == "ALPR-C901":
            fetching.set()
            assert release.wait(5), "second call did not finish"
            if first_fails:
                raise RuntimeError("synthetic overlapping fetch failure")
        return [(cursor.case_id,)]

    monkeypatch.setattr(cursor_type, "fetchall", fetchall)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(call, "ALPR-C901")
        try:
            assert fetching.wait(5), "first call did not start"
            second = pool.submit(call, "ALPR-C902").result(timeout=5)
        finally:
            release.set()
        if first_fails:
            with pytest.raises(harness.ToolDenied, match="ALPR_LIVE_VIEW_FAILED:RuntimeError"):
                first.result(timeout=5)
        else:
            assert evidence(first.result(timeout=5)) == [
                ("ownership_at_event", "ALPR-C901", "synthetic-query-ALPR-C901")]
    assert evidence(second) == [("ownership_at_event", "ALPR-C902", "synthetic-query-ALPR-C902")]


def test_remediation_keeps_each_successful_inner_query(monkeypatch, cursor_type):
    monkeypatch.setattr(cursor_type, "fetchall", lambda cursor: [])
    result = call("ALPR-C901", "agent-alpr-remediation")
    assert [evidence(inner) for inner in result["inner_calls"]] == [
        [("ownership_at_event", "ALPR-C901", "synthetic-query-ALPR-C901")],
        [("charges_notices_payments", "ALPR-C901", "synthetic-query-ALPR-C901")],
        [("remediation_status", "ALPR-C901", "synthetic-query-ALPR-C901")],
        [("policy_at_event", "ALPR-C901", "synthetic-query-ALPR-C901")],
    ]
    assert result["findings"]["outcome"] == "CASE_NOT_FOUND"
    assert alpr.consume_query_ids() == []
