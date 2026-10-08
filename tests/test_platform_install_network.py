from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws

from backend.dynamo_store import DynamoStore
from backend.foundation_runs import get
from scripts import platform_install as install
from tests.bootstrap_support import CloudFormation, Control, Target

VPC = {"networkMode": "VPC", "networkModeConfig": {"subnets": ["subnet-a", "subnet-b"], "securityGroups": ["sg-runtime"]}}


@pytest.fixture
def fresh(monkeypatch):
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1",
                                  aws_access_key_id="testing", aws_secret_access_key="testing")
        resource.create_table(TableName="test-state", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"} for key in ("pk", "sk")])
        store = DynamoStore("test-state", resource)
        store.initialize()
        target = Target()
        target.operations_key = "platformOperations"
        target.state["app"] = {"outputs": {"StateTable": "test-state"}}
        target.cf = CloudFormation(target)
        control = Control(target)
        control.get_evaluator = lambda **kwargs: {
            "evaluatorId": "Builtin.Correctness", "evaluatorArn": "arn:aws:bedrock-agentcore:::evaluator/Builtin.Correctness"}
        target.session = SimpleNamespace(resource=lambda _: resource, client=lambda _: control)
        artifact = {"bucket": "test-artifacts", "key": "test.zip", "version_id": "test-version", "sha256": "a" * 64}
        uploads = []
        monkeypatch.setattr(install.journey, "upload", lambda _: uploads.append(1) or ("test-artifacts", "test.zip", artifact))
        yield target, store, uploads


def live(store):
    with store.tx() as db:
        return get(db, "journey-platform")


def test_fresh_install_without_a_network_choice_stays_public(fresh):
    target, store, _ = fresh
    install.prepare(target)
    assert target.state["journeyPlatform"]["network"] == {"networkMode": "PUBLIC"}
    assert live(store)["network"] == {"networkMode": "PUBLIC"}


def test_fresh_install_uses_the_configured_agent_network(fresh):
    target, store, _ = fresh
    target.state["agentNetwork"] = VPC
    install.prepare(target)
    assert target.state["journeyPlatform"]["network"] == VPC
    assert live(store)["network"] == VPC


def test_invalid_agent_network_choice_fails_before_any_installation_work(fresh):
    target, store, uploads = fresh
    target.state["agentNetwork"] = {"networkMode": "VPC", "networkModeConfig": {"subnets": [], "securityGroups": ["sg-1"]}}
    with pytest.raises(ValueError, match="network"):
        install.prepare(target)
    assert uploads == [] and "journeyPlatform" not in target.state and live(store) is None


def test_rerun_preserves_the_saved_network_choice(fresh):
    target, store, uploads = fresh
    target.state["agentNetwork"] = VPC
    install.prepare(target)
    target.state["agentNetwork"] = {"networkMode": "PUBLIC"}
    install.prepare(target)
    assert uploads == [1]
    assert target.state["journeyPlatform"]["network"] == VPC
    assert live(store)["network"] == VPC
