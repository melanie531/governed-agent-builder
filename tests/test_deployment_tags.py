"""Offline contracts for retention tags; only external AWS calls are replaced."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from backend.journey_cloud import JourneyCloud
from infra import serverless
from infra.identity import template as identity_template
from infra.journey import template as journey_template
from scripts import serverless_deploy


# Independently checked against CloudFormation resource specification 265.0.0.
LIST_TAG_TYPES = {
    "AWS::S3::Bucket", "AWS::DynamoDB::Table", "AWS::SQS::Queue",
    "AWS::CloudFront::Distribution", "AWS::CloudFront::Function",
    "AWS::IAM::Role", "AWS::Lambda::Function", "AWS::Lambda::EventSourceMapping",
    "AWS::Logs::LogGroup", "AWS::CloudWatch::Alarm",
}
MAP_TAG_PROPERTIES = {
    "AWS::ApiGatewayV2::Api": "Tags",
    "AWS::ApiGatewayV2::Stage": "Tags",
    "AWS::Cognito::UserPool": "UserPoolTags",
}
UNTAGGABLE_TYPES = {
    "AWS::S3::BucketPolicy", "AWS::SQS::QueuePolicy",
    "AWS::CloudFront::OriginAccessControl", "AWS::CloudFront::ResponseHeadersPolicy",
    "AWS::Cognito::UserPoolClient", "AWS::Cognito::UserPoolDomain",
    "AWS::Cognito::UserPoolGroup", "AWS::Cognito::ManagedLoginBranding",
    "AWS::ApiGatewayV2::Authorizer", "AWS::ApiGatewayV2::Integration",
    "AWS::ApiGatewayV2::Route", "AWS::Lambda::Permission",
}
SETTINGS = {
    "account": "123456789012", "region": "us-east-1",
    "bucket": "synthetic-evidence", "log_group": "/synthetic/journey",
    "runtime_role": "arn:aws:iam::123456789012:role/synthetic-runtime",
    "network": {"networkMode": "PUBLIC"},
    "artifact": {"bucket": "synthetic-artifacts", "key": "journey/foundation/base.zip", "version_id": "1"},
    "evaluator_id": "Builtin.Correctness",
    "evaluator_arn": "arn:aws:bedrock-agentcore:::evaluator/Builtin.Correctness",
}


def build_template(kind):
    if kind == "artifacts":
        return serverless.artifacts_template()
    if kind == "identity":
        return identity_template("https://synthetic.example.test")
    if kind == "journey":
        return journey_template("synthetic-provider-arn", "synthetic-secret-arn")
    if kind == "app-journey":
        return serverless.template(journey=deepcopy(SETTINGS))
    return serverless.template()


@pytest.mark.parametrize("kind", ["artifacts", "identity", "journey", "app", "app-journey"])
def test_every_supported_resource_has_retention_tag_with_cfn_property_shape(kind):
    resources = build_template(kind)["Resources"]
    for name, resource in resources.items():
        resource_type, props = resource["Type"], resource["Properties"]
        if resource_type in LIST_TAG_TYPES:
            tags = props.get("Tags")
            assert isinstance(tags, list), (kind, name, "Tags must be a list", tags)
            assert all(isinstance(t, dict) and set(t) == {"Key", "Value"} for t in tags), name
            assert [t["Value"] for t in tags if t["Key"] == "auto-delete"] == ["no"], name
        elif resource_type in MAP_TAG_PROPERTIES:
            key = MAP_TAG_PROPERTIES[resource_type]
            tags = props.get(key)
            assert isinstance(tags, dict), (kind, name, key, "must be a map", tags)
            assert tags.get("auto-delete") == "no", (kind, name, tags)
            if key == "UserPoolTags":
                assert "Tags" not in props
                assert tags.items() >= {
                    "project": "governed-agent-builder",
                    "managedBy": "cloudformation",
                }.items()
                assert "owner" not in tags
        else:
            assert resource_type in UNTAGGABLE_TYPES, f"Review tag support for {resource_type}"
            assert "Tags" not in props and "UserPoolTags" not in props, name
    serverless_deploy.safety(build_template(kind))


def test_template_tags_preserve_existing_values_and_override_conflicting_retention(monkeypatch):
    original = serverless.bucket
    existing = [{"Key": "Name", "Value": {"Ref": "AWS::StackName"}},
                {"Key": "auto-delete", "Value": "yes"}]

    def tagged_bucket():
        resource = original()
        resource["Properties"]["Tags"] = deepcopy(existing)
        return resource

    monkeypatch.setattr(serverless, "bucket", tagged_bucket)
    for body in (serverless.artifacts_template(), serverless.template()):
        for resource in body["Resources"].values():
            if resource["Type"] == "AWS::S3::Bucket":
                tags = resource["Properties"]["Tags"]
                assert tags == [existing[0], {"Key": "auto-delete", "Value": "no"}]
    assert existing[1]["Value"] == "yes"


def test_resource_tags_are_independent_between_resources_and_renders():
    resources = serverless.template()["Resources"]
    resources["Web"]["Properties"]["Tags"][0]["Value"] = "changed"
    resources["Api"]["Properties"]["Tags"]["auto-delete"] = "changed"
    assert resources["Exports"]["Properties"]["Tags"] == [{"Key": "auto-delete", "Value": "no"}]
    assert resources["Stage"]["Properties"]["Tags"] == {"auto-delete": "no"}
    assert serverless.template()["Resources"]["Web"]["Properties"]["Tags"] == [
        {"Key": "auto-delete", "Value": "no"}]
    assert serverless.template()["Resources"]["Api"]["Properties"]["Tags"] == {"auto-delete": "no"}


@pytest.mark.parametrize("name,field", [("Web", "Tags"), ("Api", "Tags"), ("Pool", "UserPoolTags")])
@pytest.mark.parametrize("invalid", [None, [], {}, [{"Key": "auto-delete", "Value": "yes"}],
                                    {"auto-delete": "yes"}, "no", True,
                                    [{"Key": "auto-delete", "Value": "no"},
                                     {"Key": "auto-delete", "Value": "yes"}]])
def test_safety_rejects_missing_malformed_or_conflicting_retention_tags(name, field, invalid):
    body = serverless.template()
    if invalid is None:
        body["Resources"][name]["Properties"].pop(field, None)
    else:
        body["Resources"][name]["Properties"][field] = deepcopy(invalid)
    before = deepcopy(body)
    with pytest.raises(RuntimeError, match=f"{name}.*auto-delete"):
        serverless_deploy.safety(body)
    assert body == before, "Safety checks must reject, not repair submitted templates"


@pytest.mark.parametrize("name,field,value", [
    ("Web", "Tags", {"auto-delete": "no"}),
    ("Api", "Tags", [{"Key": "auto-delete", "Value": "no"}]),
    ("Pool", "UserPoolTags", [{"Key": "auto-delete", "Value": "no"}]),
])
def test_safety_rejects_valid_tag_values_in_wrong_cfn_shape(name, field, value):
    body = serverless.template()
    body["Resources"][name]["Properties"][field] = value
    with pytest.raises(RuntimeError, match=f"{name}.*auto-delete"):
        serverless_deploy.safety(body)


@pytest.mark.parametrize("operation", ["create", "update"])
def test_stack_requests_include_retention_and_existing_ownership_tags(monkeypatch, operation):
    requests = []
    stack = {
        "StackId": "synthetic-stack", "StackStatus": "CREATE_COMPLETE",
        "Tags": [{"Key": "architecture", "Value": "managed-serverless"}], "Outputs": [],
    }

    def describe(**kwargs):
        if operation == "create" and not requests:
            raise ClientError({"Error": {"Code": "ValidationError", "Message": "does not exist"}}, "DescribeStacks")
        return {"Stacks": [stack]}

    monkeypatch.setattr(serverless_deploy, "TARGET", SimpleNamespace(
        check_stacks=lambda: None, check_stack=lambda _: None, save=lambda *args: None))
    monkeypatch.setattr(serverless_deploy, "CF", SimpleNamespace(
        describe_stacks=describe, create_stack=lambda **kw: requests.append(kw),
        update_stack=lambda **kw: requests.append(kw)))
    assert serverless_deploy.deploy("artifacts", serverless.artifacts_template()) == {}
    assert len(requests) == 1
    assert requests[0]["Tags"] == [
        {"Key": "project", "Value": "governed-agent-builder"},
        {"Key": "architecture", "Value": "managed-serverless"},
        {"Key": "auto-delete", "Value": "no"},
    ]


def test_journey_runtime_creation_includes_retention_and_existing_ownership_tags():
    requests, writes = [], []
    runtime = {
        "agentRuntimeId": "gab_journey_synthetic", "agentRuntimeVersion": "1",
        "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/gab_journey_synthetic",
    }

    def create(**kwargs):
        requests.append(kwargs)
        return runtime

    def put(**kwargs):
        writes.append(kwargs)
        return {"VersionId": "synthetic-version"}

    clients = {
        "bedrock-agentcore-control": SimpleNamespace(create_agent_runtime=create),
        "bedrock-agentcore": SimpleNamespace(),
        "s3": SimpleNamespace(put_object=put),
    }
    cloud = JourneyCloud(deepcopy(SETTINGS), session=SimpleNamespace(client=lambda name, **kw: clients[name]))
    manifest = {"artifact": deepcopy(SETTINGS["artifact"]), "agent_id": "synthetic-agent", "workspace": "research"}
    binding = cloud.create(manifest, "synthetic-token")
    assert binding["arn"] == runtime["agentRuntimeArn"]
    assert writes[0]["ServerSideEncryption"] == "AES256"
    assert requests[0]["clientToken"] == "synthetic-token"
    assert requests[0]["tags"] == {
        "project": "governed-agent-builder", "journey": "create-agent",
        "agent": "synthetic-agent", "workspace": "research", "auto-delete": "no",
    }
