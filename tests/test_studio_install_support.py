"""Native SDK contracts for fresh installation and interrupted-write recovery."""
import base64
import copy
import datetime
import hashlib
import json
from types import SimpleNamespace

import boto3
from botocore.stub import ANY, Stubber
import pytest

from foundation_harness.config import digest
from infra.serverless import artifacts_template
from scripts.bootstrap_support import PendingOperation
from scripts.deployment_target import DeploymentTarget, PREFIX
from scripts import studio_install_support as support


BINDING = {"account": "123456789012", "profile": "customer-profile", "region": "us-west-2"}
STACK_ID = f"arn:aws:cloudformation:us-west-2:123456789012:stack/{PREFIX}-artifacts/stack-id"


class Target:
    operations_key = "platformOperations"
    binding = BINDING
    check_stack = DeploymentTarget.check_stack

    def __init__(self, client, **state):
        self.state = copy.deepcopy(state)
        self.cf = client
        self.session = SimpleNamespace(client=lambda _: client)

    def save(self, key, value):
        self.state[key] = copy.deepcopy(value)

    def check_stacks(self):
        pass


def client(service):
    return boto3.client(service, region_name=BINDING["region"],
                        aws_access_key_id="testing", aws_secret_access_key="testing")


def absent(stub, operation, parameters):
    stub.add_client_error(operation, service_error_code="ValidationError",
                          service_message="Stack does not exist", expected_params=parameters)


def stack():
    return {
        "StackId": STACK_ID, "StackName": PREFIX + "-artifacts",
        "CreationTime": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        "StackStatus": "CREATE_COMPLETE", "Parameters": [],
        "Outputs": [{"OutputKey": "Bucket", "OutputValue": "test-artifacts"}],
        "Tags": [{"Key": key, "Value": value} for key, value in support.STACK_TAGS.items()],
    }


def test_create_stack_validates_and_records_before_dispatch():
    cf = client("cloudformation")
    target = Target(cf)
    body = artifacts_template()
    with Stubber(cf) as stub:
        absent(stub, "describe_stacks", {"StackName": PREFIX + "-artifacts"})
        stub.add_response("validate_template", {}, {"TemplateBody": ANY})
        stub.add_response("create_stack", {"StackId": STACK_ID}, {
            "StackName": PREFIX + "-artifacts", "TemplateBody": ANY,
            "Capabilities": ["CAPABILITY_IAM"],
            "Tags": stack()["Tags"], "Parameters": [], "ClientRequestToken": ANY,
            "DisableRollback": True,
        })
        stub.add_response("describe_stacks", {"Stacks": [stack()]}, {"StackName": STACK_ID})
        stub.add_response("get_template", {"TemplateBody": json.dumps(body)}, {"StackName": STACK_ID})
        assert support.install_stack(target, "artifacts", body) == {"Bucket": "test-artifacts"}
        stub.assert_no_pending_responses()
    assert target.state["artifacts"]["stackId"] == STACK_ID
    assert target.state["installationStacks"]["artifacts"]["verified"] is True
    receipt = target.state["platformOperations"]["install-stack-artifacts"]
    assert receipt["status"] == "COMPLETE" and receipt["request_token"]


def test_existing_unrecorded_stack_is_never_adopted():
    cf = client("cloudformation")
    target = Target(cf)
    with Stubber(cf) as stub:
        stub.add_response("describe_stacks", {"Stacks": [stack()]}, {"StackName": PREFIX + "-artifacts"})
        with pytest.raises(RuntimeError, match="unrecorded"):
            support.install_stack(target, "artifacts", artifacts_template())
        stub.assert_no_pending_responses()
    assert not target.state


def test_interrupted_stack_requires_native_request_token_before_recovery():
    cf = client("cloudformation")
    target = Target(cf)
    body = artifacts_template()
    request = support.stack_intent("artifacts", body, {})
    token = digest([BINDING, "install-stack-artifacts", request])
    target.state["platformOperations"] = {"install-stack-artifacts": {
        "status": "INTENT", "request": request, "request_digest": digest([BINDING, request]),
        "request_token": token,
    }}
    with Stubber(cf) as stub:
        stub.add_response("describe_stacks", {"Stacks": [stack()]}, {"StackName": PREFIX + "-artifacts"})
        stub.add_response("describe_stack_events", {"StackEvents": []}, {"StackName": STACK_ID})
        with pytest.raises(PendingOperation):
            support.recover_stacks(target)
        stub.assert_no_pending_responses()
    assert "artifacts" not in target.state
    event = {
        "StackId": STACK_ID, "StackName": PREFIX + "-artifacts", "EventId": "created",
        "Timestamp": stack()["CreationTime"], "ResourceType": "AWS::CloudFormation::Stack",
        "ClientRequestToken": token,
    }
    with Stubber(cf) as stub:
        stub.add_response("describe_stacks", {"Stacks": [stack()]}, {"StackName": PREFIX + "-artifacts"})
        stub.add_response("describe_stack_events", {"StackEvents": [event]}, {"StackName": STACK_ID})
        stub.add_response("describe_stacks", {"Stacks": [stack()]}, {"StackName": STACK_ID})
        stub.add_response("get_template", {"TemplateBody": json.dumps(body)}, {"StackName": STACK_ID})
        support.recover_stacks(target)
        stub.assert_no_pending_responses()
    assert target.state["artifacts"]["stackId"] == STACK_ID


@pytest.mark.parametrize("lost_ack", [False, True])
def test_upload_is_immutable_and_reconciles_lost_acknowledgment(lost_ack):
    s3 = client("s3")
    target = Target(s3, artifacts={"outputs": {"Bucket": "test-artifacts"}})
    content = b"approved-release"
    sha = hashlib.sha256(content).hexdigest()
    key = f"releases/{sha}/lambda.zip"
    request = {"bucket": "test-artifacts", "key": key, "sha256": sha, "size": len(content)}
    token = digest([BINDING, "install-upload-lambda", request])
    checksum = base64.b64encode(bytes.fromhex(sha)).decode()
    head = {"VersionId": "version-1", "ContentLength": len(content),
            "Metadata": {"sha256": sha, "request-token": token},
            "ServerSideEncryption": "AES256", "ChecksumSHA256": checksum}
    with Stubber(s3) as stub:
        stub.add_client_error("head_object", service_error_code="404", http_status_code=404,
                              expected_params={"Bucket": "test-artifacts", "Key": key, "ChecksumMode": "ENABLED"})
        put = {"Bucket": "test-artifacts", "Key": key, "Body": content,
               "IfNoneMatch": "*", "Metadata": head["Metadata"], "ServerSideEncryption": "AES256",
               "ChecksumSHA256": checksum, "Tagging": "auto-delete=no"}
        if lost_ack:
            stub.add_client_error("put_object", service_error_code="RequestTimeout", expected_params=put)
        else:
            stub.add_response("put_object", {"VersionId": "version-1"}, put)
        for _ in range(2):
            stub.add_response("head_object", head, {"Bucket": "test-artifacts", "Key": key, "ChecksumMode": "ENABLED"})
        receipt = support.upload_artifact(target, "lambda", key, content)
        assert receipt == {"bucket": "test-artifacts", "key": key, "version": "version-1", "digest": sha}
        stub.assert_no_pending_responses()
    # Completed upload is verified from its current version and never written again.
    with Stubber(s3) as stub:
        stub.add_response("head_object", head, {"Bucket": "test-artifacts", "Key": key, "ChecksumMode": "ENABLED"})
        assert support.upload_artifact(target, "lambda", key, content) == receipt
        stub.assert_no_pending_responses()


def test_source_digest_ignores_workstation_metadata_and_rejects_code_drift(tmp_path):
    (tmp_path / "backend").mkdir()
    path = tmp_path / "backend/main.py"
    path.write_text("value = 1\n")
    before = support.source_digest(tmp_path)
    (tmp_path / "backend/.DS_Store").write_bytes(b"personal metadata")
    (tmp_path / "README.md").write_text("documentation only")
    assert support.source_digest(tmp_path) == before
    path.write_text("value = 2\n")
    assert support.source_digest(tmp_path) != before


def rollback_target(cf):
    app_id = STACK_ID.replace("-artifacts/", "-app/")
    previous = {"Resources": {"Worker": {"Type": "AWS::Lambda::Function",
                                       "Properties": {"MemorySize": 512}}}}
    attempted = copy.deepcopy(previous)
    attempted["Resources"]["Worker"]["Properties"]["MemorySize"] = 1024
    current = {**stack(), "StackId": app_id, "StackName": PREFIX + "-app",
               "StackStatus": "UPDATE_ROLLBACK_COMPLETE"}
    request = support.stack_intent("app", attempted, {}, prior_stack=app_id)
    record = {"status": "COMPLETE", "request": request, "request_token": "failed-activation",
              "request_digest": digest([BINDING, request]), "result": {"stack_id": app_id}}
    target = Target(cf, app={"stackId": app_id, "outputs": {"Bucket": "test-artifacts"}},
                    installationStacks={"app": {"verified": True, "stack_id": app_id,
                                                 "template_digest": digest(previous)}},
                    platformOperations={
                        "install-stack-app": {"request": support.stack_intent("app", previous, {})},
                        "install-stack-activate": record,
                    })
    complete = {"StackId": app_id, "StackName": PREFIX + "-app", "EventId": "rollback-complete",
                "Timestamp": stack()["CreationTime"], "ResourceType": "AWS::CloudFormation::Stack",
                "PhysicalResourceId": app_id, "ResourceStatus": "UPDATE_ROLLBACK_COMPLETE",
                "ClientRequestToken": "failed-activation"}
    failed = {**complete, "EventId": "memory-rejected", "ResourceType": "AWS::Lambda::Function",
              "LogicalResourceId": "Worker", "ResourceStatus": "UPDATE_FAILED",
              "ResourceStatusReason": "'MemorySize' value must have value less than or equal to 512"}
    return target, current, previous, [complete, failed]


def test_owned_activation_rollback_is_reconciled_without_repeating_update():
    cf = client("cloudformation")
    target, current, previous, events = rollback_target(cf)
    with Stubber(cf) as stub:
        stub.add_response("describe_stacks", {"Stacks": [current]}, {"StackName": current["StackId"]})
        stub.add_response("get_template", {"TemplateBody": json.dumps(previous)},
                          {"StackName": current["StackId"]})
        stub.add_response("describe_stack_events", {"StackEvents": events},
                          {"StackName": current["StackId"]})
        support.recover_stacks(target)
        stub.assert_no_pending_responses()
    resolution = target.state["installationRollbacks"]["activate"]
    assert resolution["worker_memory_limit"] == 512
    assert resolution["request_token"] == "failed-activation"
    assert target.reconciled_rollback_stack_id == current["StackId"]
    assert target.state["platformOperations"]["install-stack-activate"]["request_token"] == "failed-activation"


@pytest.mark.parametrize("drift", ["template", "token"])
def test_unrelated_rollback_cannot_be_accepted(drift):
    cf = client("cloudformation")
    target, current, previous, events = rollback_target(cf)
    if drift == "template":
        previous["Resources"]["Worker"]["Properties"]["MemorySize"] = 256
    else:
        events[0]["ClientRequestToken"] = "unrelated-update"
    with Stubber(cf) as stub:
        stub.add_response("describe_stacks", {"Stacks": [current]}, {"StackName": current["StackId"]})
        stub.add_response("get_template", {"TemplateBody": json.dumps(previous)},
                          {"StackName": current["StackId"]})
        if drift == "token":
            stub.add_response("describe_stack_events", {"StackEvents": events},
                              {"StackName": current["StackId"]})
        with pytest.raises(RuntimeError):
            support.recover_stacks(target)
        stub.assert_no_pending_responses()
    assert "installationRollbacks" not in target.state
