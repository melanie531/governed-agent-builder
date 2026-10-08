import json
import os
from pathlib import Path
import subprocess
import sys

import boto3
import pytest
from moto import mock_aws

from backend.dynamo_store import DynamoStore
from backend.foundation_runs import get, put
from scripts import configure_model_policy as configure
from tests.bootstrap_support import Target

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def installed():
    with mock_aws():
        session = boto3.Session(aws_access_key_id="testing", aws_secret_access_key="testing", region_name="us-east-1")
        resource = session.resource("dynamodb")
        resource.create_table(TableName="test-state", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"} for key in ("pk", "sk")])
        store = DynamoStore("test-state", resource)
        store.initialize()
        settings = {"enabled": True, "account": "123456789012", "region": "us-east-1", "network": {"networkMode": "PUBLIC"}}
        with store.tx() as db:
            put(db, "journey-platform", settings)
        target = Target()
        target.session = session
        target.state.update(app={"outputs": {"StateTable": "test-state"}}, journeyPlatform=settings)
        yield target, store


def live(store):
    with store.tx() as db:
        return get(db, "journey-platform")


def test_default_action_is_a_plan_that_writes_nothing(installed, capsys):
    target, store = installed
    result = configure.run(target, "au", apply=False)
    assert result == {"current": "global", "requested": "au", "applied": False,
                      "would_write": ["deployment state journeyPlatform.model_policy", "live journey-platform settings model_policy"]}
    assert target.saves == [] and "model_policy" not in live(store)
    assert json.loads(capsys.readouterr().out)["requested"] == "au"


@pytest.mark.parametrize("policy", ["au", "global"])
def test_apply_updates_bound_state_and_live_record_consistently(installed, policy):
    target, store = installed
    configure.run(target, policy, apply=True)
    assert target.state["journeyPlatform"]["model_policy"] == policy
    assert live(store) == target.state["journeyPlatform"]


def test_apply_refuses_when_live_record_and_bound_state_disagree(installed):
    target, store = installed
    with store.tx() as db:
        put(db, "journey-platform", {**target.state["journeyPlatform"], "enabled": False})
    with pytest.raises(RuntimeError, match="differ"):
        configure.run(target, "au", apply=True)
    assert target.saves == []


def test_apply_resumes_after_live_record_was_already_updated(installed):
    target, store = installed
    with store.tx() as db:
        put(db, "journey-platform", {**target.state["journeyPlatform"], "model_policy": "au"})
    configure.run(target, "au", apply=True)
    assert target.state["journeyPlatform"]["model_policy"] == "au" == live(store)["model_policy"]


def test_unknown_policy_and_uninstalled_platform_are_rejected(installed):
    target, _ = installed
    with pytest.raises(ValueError, match="model policy"):
        configure.run(target, "us", apply=True)
    del target.state["journeyPlatform"]
    with pytest.raises(RuntimeError, match="not installed"):
        configure.run(target, "au", apply=False)
    assert target.saves == []


def test_help_works_without_aws_credentials():
    env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
    env["AWS_CONFIG_FILE"] = env["AWS_SHARED_CREDENTIALS_FILE"] = os.devnull
    result = subprocess.run([sys.executable, "scripts/configure_model_policy.py", "--help"],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "--apply" in result.stdout and "{global,au}" in result.stdout
