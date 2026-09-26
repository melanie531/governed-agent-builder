"""SDK contracts for log retention discovered during hosted Snowflake acceptance."""
from copy import deepcopy
from types import SimpleNamespace

import boto3
from botocore.stub import Stubber
import pytest

from backend.journey_cloud import JourneyCloud
from backend.journey import Journey
from infra.serverless import template
from tests.test_deployment_tags import SETTINGS


ACCOUNT, REGION = "123456789012", "us-east-1"
RUNTIME_ID = "gab_journey_demo-ABC123"
BINDING = {"id": RUNTIME_ID, "arn": f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:runtime/{RUNTIME_ID}"}
NAME = "/aws/bedrock-agentcore/runtimes/" + RUNTIME_ID + "-DEFAULT"
ARN = f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:{NAME}"
TAGS = {"project": "governed-agent-builder", "journey": "create-agent", "auto-delete": "no",
        "agent": "demo", "workspace": "research"}


def setup_cloud():
    session = boto3.Session(aws_access_key_id="testing", aws_secret_access_key="testing", region_name=REGION)
    logs = session.client("logs")
    cloud = object.__new__(JourneyCloud)
    cloud.settings = {"account": ACCOUNT, "region": REGION}
    cloud.control = SimpleNamespace(list_tags_for_resource=lambda **_: {"tags": deepcopy(TAGS)})
    cloud.session = SimpleNamespace(client=lambda service, **_: logs if service == "logs" else None)
    return cloud, Stubber(logs)


def group(retention=None):
    return {"logGroupName": NAME, "arn": ARN + ":*", **({"retentionInDays": retention} if retention else {})}


def test_new_runtime_logs_are_tagged_at_creation_and_have_bounded_retention():
    cloud, stub = setup_cloud()
    with stub:
        stub.add_response("describe_log_groups", {"logGroups": []}, {"logGroupNamePrefix": NAME})
        stub.add_response("create_log_group", {}, {"logGroupName": NAME, "tags": TAGS})
        stub.add_response("put_retention_policy", {}, {"logGroupName": NAME, "retentionInDays": 14})
        cloud.provision_runtime_logs(BINDING)
        stub.assert_no_pending_responses()


def test_existing_service_created_group_is_reconciled_without_recreation():
    cloud, stub = setup_cloud()
    with stub:
        stub.add_response("describe_log_groups", {"logGroups": [group()]}, {"logGroupNamePrefix": NAME})
        stub.add_response("list_tags_for_resource", {"tags": {"custom": "preserved"}}, {"resourceArn": ARN})
        stub.add_response("tag_resource", {}, {"resourceArn": ARN, "tags": TAGS})
        stub.add_response("put_retention_policy", {}, {"logGroupName": NAME, "retentionInDays": 14})
        cloud.provision_runtime_logs(BINDING)
        stub.assert_no_pending_responses()


def test_compliant_group_requires_only_reads():
    cloud, stub = setup_cloud()
    with stub:
        stub.add_response("describe_log_groups", {"logGroups": [group(14)]}, {"logGroupNamePrefix": NAME})
        stub.add_response("list_tags_for_resource", {"tags": TAGS}, {"resourceArn": ARN})
        cloud.provision_runtime_logs(BINDING)
        stub.assert_no_pending_responses()


def test_service_creation_race_is_reconciled_by_reading_the_exact_group():
    cloud, stub = setup_cloud()
    with stub:
        stub.add_response("describe_log_groups", {"logGroups": []}, {"logGroupNamePrefix": NAME})
        stub.add_client_error("create_log_group", "ResourceAlreadyExistsException",
                              expected_params={"logGroupName": NAME, "tags": TAGS})
        stub.add_response("describe_log_groups", {"logGroups": [group()]}, {"logGroupNamePrefix": NAME})
        stub.add_response("list_tags_for_resource", {"tags": {}}, {"resourceArn": ARN})
        stub.add_response("tag_resource", {}, {"resourceArn": ARN, "tags": TAGS})
        stub.add_response("put_retention_policy", {}, {"logGroupName": NAME, "retentionInDays": 14})
        cloud.provision_runtime_logs(BINDING)
        stub.assert_no_pending_responses()


def test_deployment_cannot_advance_to_smoke_when_log_provisioning_fails():
    journey = object.__new__(Journey)
    def denied(binding):
        raise PermissionError("Log provisioning denied")
    journey.cloud = SimpleNamespace(ready=lambda binding: True, provision_runtime_logs=denied)
    with pytest.raises(PermissionError, match="Log provisioning denied"):
        journey.perform({"kind": "deploy", "phase": "WAIT_RUNTIME", "binding": BINDING}, {}, None, False)


def test_log_group_with_conflicting_owner_is_not_mutated():
    cloud, stub = setup_cloud()
    with stub:
        stub.add_response("describe_log_groups", {"logGroups": [group()]}, {"logGroupNamePrefix": NAME})
        stub.add_response("list_tags_for_resource", {"tags": {"project": "another-project"}}, {"resourceArn": ARN})
        with pytest.raises(ValueError, match="owner"):
            cloud.provision_runtime_logs(BINDING)
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("binding", [
    {**BINDING, "id": "another-runtime"},
    {**BINDING, "arn": BINDING["arn"].replace(ACCOUNT, "999999999999")},
])
def test_log_mutation_rejects_wrong_runtime_binding(binding):
    cloud, stub = setup_cloud()
    with stub, pytest.raises(ValueError, match="binding"):
        cloud.provision_runtime_logs(binding)
    stub.assert_no_pending_responses()


def test_worker_can_manage_only_journey_runtime_log_groups():
    resources = template(journey=deepcopy(SETTINGS))["Resources"]
    policy = next(p for p in resources["WorkerRole"]["Properties"]["Policies"]
                  if p["PolicyName"] == "JourneyDeployment")["PolicyDocument"]["Statement"]
    for action in ("logs:CreateLogGroup", "logs:TagResource", "logs:ListTagsForResource", "logs:PutRetentionPolicy"):
        allowed = [s for s in policy if action in s["Action"]]
        assert allowed, action
        assert all(s["Resource"] == f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/bedrock-agentcore/runtimes/gab_journey_*"
                   for s in allowed)
