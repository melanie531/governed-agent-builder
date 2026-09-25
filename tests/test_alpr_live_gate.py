"""Tests for the ALPR live wiring: transport gate, probe plan, entrypoint hook.

OFFLINE / NOT LIVE: nothing here logs into Snowflake. These tests pin the
live-selection contract (explicit opt-in, fail closed, bootstrap identity
forbidden, private key never read at import/validation time) and the shape of
the prepared live probe plan (positive: four approved views; negative: base
tables + gold via zero-row SELECT only; no write probes). Actually running
the probes requires GRANT ROLE GAB_QUERY_READONLY TO USER GAB_BOOTSTRAP by an
admin and an explicit --live invocation, which this slice does not perform.
"""
import pytest

from backend.alpr_live import (
    AlprLiveConfigError,
    LIVE_PROBE_PLAN,
    LiveSqlApiTransport,
    build_reader,
)
from backend.alpr_reader import AlprReadOnlyReader
from tests.alpr_fixture import FakeSqlApiTransport

VALID = {
    "mode": "live",
    "account_url": "https://tcljaka-hr19243.snowflakecomputing.com",
    "user": "GAB_QUERY_USER",
    "role": "GAB_QUERY_READONLY",
    "warehouse": "GAB_QUERY_WH",
    "private_key_path": "/nonexistent/key.p8",
    "live_confirmed": True,
}


def cfg(**overrides):
    merged = dict(VALID)
    merged.update(overrides)
    return merged


# ------------------------------------------------------------ entry gate
def test_default_config_fails_closed():
    with pytest.raises(AlprLiveConfigError, match="mode"):
        build_reader({})


def test_live_requires_explicit_confirmation():
    with pytest.raises(AlprLiveConfigError, match="live_confirmed"):
        build_reader(cfg(live_confirmed=False))


def test_bootstrap_identities_are_refused():
    with pytest.raises(AlprLiveConfigError, match="[Bb]ootstrap"):
        build_reader(cfg(user="GAB_BOOTSTRAP"))
    with pytest.raises(AlprLiveConfigError, match="[Bb]ootstrap"):
        build_reader(cfg(role="GAB_BOOTSTRAP_ROLE"))


def test_only_readonly_role_is_accepted():
    with pytest.raises(AlprLiveConfigError, match="role"):
        build_reader(cfg(role="ACCOUNTADMIN"))


def test_offline_mode_requires_injected_transport_no_silent_fixture():
    reader = build_reader({"mode": "offline"}, transport=FakeSqlApiTransport())
    assert isinstance(reader, AlprReadOnlyReader)
    with pytest.raises(AlprLiveConfigError, match="transport"):
        build_reader({"mode": "offline"})  # no implicit fixture fallback


def test_live_build_validates_config_without_reading_the_key():
    """Config validation must not attempt key IO; key is read only on first request."""
    transport = LiveSqlApiTransport(cfg())
    assert transport.calls == 0
    assert transport._key_loaded is False  # noqa: SLF001 — pinned contract


def test_missing_config_fields_each_fail_closed():
    for field in ("account_url", "user", "role", "warehouse", "private_key_path"):
        broken = cfg()
        broken.pop(field)
        with pytest.raises(AlprLiveConfigError, match=field):
            build_reader(broken)


# ------------------------------------------------------------ probe plan
def test_probe_plan_positive_covers_exactly_the_four_views():
    positives = [p for p in LIVE_PROBE_PLAN if p["expect"] == "rows_allowed"]
    assert sorted(p["object"].rsplit(".", 1)[1] for p in positives) == [
        "ACCOUNT_VEHICLE", "BILLING_NOTICE", "POLICY", "REMEDIATION"]
    for probe in positives:
        assert probe["object"].startswith("GAB_DEMO_DB.ALPR_APPROVED.")
        assert probe["sql"].startswith("SELECT")


def test_probe_plan_negatives_cover_base_tables_and_gold_zero_row_only():
    negatives = [p for p in LIVE_PROBE_PLAN if p["expect"] == "denied"]
    objects = {p["object"] for p in negatives}
    assert "GAB_DEMO_DB.ALPR_DEMO.EXPECTED_CASE_RESULTS" in objects
    assert any(o.startswith("GAB_DEMO_DB.ALPR_DEMO.") and not o.endswith(
        "EXPECTED_CASE_RESULTS") for o in objects)
    for probe in negatives:
        sql = probe["sql"].upper()
        assert sql.startswith("SELECT")
        assert "LIMIT 0" in sql, "negative probes must be zero-row SELECTs"


def test_probe_plan_contains_no_write_statements():
    for probe in LIVE_PROBE_PLAN:
        sql = probe["sql"].upper()
        for verb in ("INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "DROP",
                     "GRANT", "ALTER", "TRUNCATE", "CALL", "PUT ", "COPY"):
            assert verb not in sql, (verb, probe)


def test_probe_plan_declares_bootstrap_forbidden():
    assert all(p["role"] == "GAB_QUERY_READONLY" for p in LIVE_PROBE_PLAN)
    assert not any("BOOTSTRAP" in p["role"] for p in LIVE_PROBE_PLAN)
