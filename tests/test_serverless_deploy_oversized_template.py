"""Templates above the CloudFormation body limit must deploy via a versioned S3 TemplateURL."""
import copy
import hashlib
import json
from types import SimpleNamespace

from botocore.exceptions import ClientError
import pytest

from infra import serverless
from scripts import serverless_deploy

LIMIT = 51200


def oversized_template():
    body = serverless.artifacts_template()
    source = json.dumps(body["Resources"]["Releases"])
    index = 0
    while len(json.dumps(body, separators=(",", ":")).encode()) <= LIMIT:
        body["Resources"][f"Padding{index}"] = json.loads(source)
        index += 1
    return body


@pytest.mark.parametrize("operation", ["create", "update"])
def test_oversized_template_uploads_to_s3_and_uses_template_url(monkeypatch, operation):
    requests, uploads = [], []
    stack = {"StackId": "synthetic", "StackStatus": "CREATE_COMPLETE",
             "Tags": [{"Key": "architecture", "Value": "managed-serverless"}], "Outputs": []}

    def describe(**kwargs):
        if operation == "create" and not requests:
            raise ClientError({"Error": {"Code": "ValidationError", "Message": "does not exist"}}, "DescribeStacks")
        return {"Stacks": [copy.deepcopy(stack)]}

    def put_object(**kwargs):
        uploads.append(kwargs)
        return {"VersionId": "test-version"}

    monkeypatch.setattr(serverless_deploy, "TARGET", SimpleNamespace(
        check_stacks=lambda: None, check_stack=lambda _: None, save=lambda *args: None,
        state={"artifacts": {"outputs": {"Bucket": "synthetic-releases"}}},
        binding={"region": "us-west-2"}))
    monkeypatch.setattr(serverless_deploy, "CF", SimpleNamespace(
        describe_stacks=describe, create_stack=lambda **kw: requests.append(kw),
        update_stack=lambda **kw: requests.append(kw)))
    monkeypatch.setattr(serverless_deploy, "SESSION", SimpleNamespace(
        client=lambda service: SimpleNamespace(put_object=put_object)))

    body = oversized_template()
    serverless_deploy.deploy("app", body)

    assert len(requests) == 1
    assert "TemplateBody" not in requests[0]
    content = json.dumps(body, separators=(",", ":")).encode()
    key = "templates/governed-agent-builder-serverless-app/" + hashlib.sha256(content).hexdigest() + ".json"
    assert requests[0]["TemplateURL"] == (
        "https://synthetic-releases.s3.us-west-2.amazonaws.com/" + key + "?versionId=test-version")
    assert uploads == [{"Bucket": "synthetic-releases", "Key": key, "Body": content,
                        "ServerSideEncryption": "AES256", "ContentType": "application/json",
                        "Tagging": "auto-delete=no"}]


def test_small_template_still_uses_inline_template_body(monkeypatch):
    requests = []
    monkeypatch.setattr(serverless_deploy, "TARGET", SimpleNamespace(
        check_stacks=lambda: None, check_stack=lambda _: None, save=lambda *args: None,
        state={"artifacts": {"outputs": {"Bucket": "synthetic-releases"}}},
        binding={"region": "us-west-2"}))

    def describe(**kwargs):
        if not requests:
            raise ClientError({"Error": {"Code": "ValidationError", "Message": "does not exist"}}, "DescribeStacks")
        return {"Stacks": [{"StackId": "synthetic", "StackStatus": "CREATE_COMPLETE",
                            "Tags": [{"Key": "architecture", "Value": "managed-serverless"}], "Outputs": []}]}

    monkeypatch.setattr(serverless_deploy, "CF", SimpleNamespace(
        describe_stacks=describe, create_stack=lambda **kw: requests.append(kw),
        update_stack=lambda **kw: requests.append(kw)))
    monkeypatch.setattr(serverless_deploy, "SESSION", SimpleNamespace(
        client=lambda service: (_ for _ in ()).throw(AssertionError("no S3 upload for small templates"))))

    serverless_deploy.deploy("artifacts", serverless.artifacts_template())
    assert len(requests) == 1
    assert "TemplateURL" not in requests[0]
    assert len(requests[0]["TemplateBody"].encode()) <= LIMIT
