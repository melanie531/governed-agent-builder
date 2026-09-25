"""Tests for the ALPR investigation entrypoint (platform hook).

OFFLINE / NOT LIVE. The entrypoint is a NEW module (runtime.mcp_specialist
and every existing entrypoint are untouched — original platform behaviour is
preserved). Live is an explicit opt-in: no flag combination silently reaches
the network, and offline explicitly labels itself fixture/not-live.
"""
import json

import pytest

from backend.alpr_entrypoint import main


def test_offline_eval_runs_six_cases(capsys):
    exit_code = main(["--mode", "offline-fixture", "--eval"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["not_live"] is True
    assert payload["synthetic"] is True
    assert payload["passed"] == 6 and payload["total"] == 6


def test_offline_single_case_report(capsys):
    exit_code = main(["--mode", "offline-fixture", "--case", "CASE-006"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["finding"] == "POST_EVENT_TRANSFER_WRONG_PAYMENT"
    assert payload["advisory_only"] is True
    assert payload["actions_executed"] == []


def test_no_mode_fails_closed():
    with pytest.raises(SystemExit):
        main([])


def test_live_requires_config_and_confirmation():
    exit_code = main(["--mode", "live", "--case", "CASE-001"])
    assert exit_code == 2  # refused: no config, no confirmation — fail closed


def test_live_never_uses_fixture(tmp_path, capsys):
    config = tmp_path / "live.json"
    config.write_text(json.dumps({"mode": "live"}))  # incomplete on purpose
    exit_code = main(["--mode", "live", "--config", str(config), "--case", "CASE-001"])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "live_confirmed" in err or "missing" in err
