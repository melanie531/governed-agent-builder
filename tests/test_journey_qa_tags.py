from types import SimpleNamespace
import sys

import boto3
from moto import mock_aws

from scripts import journey_qa_identity


@mock_aws
def test_qa_secret_parameters_have_required_retention_tag(tmp_path, monkeypatch):
    session = boto3.Session(region_name="us-east-1")
    cognito = SimpleNamespace(
        admin_create_user=lambda **kwargs: {"User": {"Attributes": [{"Name": "sub", "Value": "qa-subject"}]}},
        admin_set_user_password=lambda **kwargs: None,
        admin_add_user_to_group=lambda **kwargs: None,
    )
    target = SimpleNamespace(
        path=tmp_path / "release.json",
        session=SimpleNamespace(client=lambda name: cognito if name == "cognito-idp" else session.client(name)),
        state={"app": {"outputs": {
            "UserPoolId": "us-east-1_test", "ClientId": "test-client",
            "ApplicationOrigin": "https://example.test",
        }}},
    )
    target.save = target.state.__setitem__
    monkeypatch.setattr(journey_qa_identity, "DeploymentTarget", lambda *args: target)
    monkeypatch.setattr(sys, "argv", [
        "journey_qa_identity.py", "--expected-account", "123456789012",
        "--profile", "default", "--region", "us-east-1",
        "--state", str(target.path),
    ])
    journey_qa_identity.main()
    prefix = target.state["journeyQA"]["parameterPrefix"]
    for suffix in ("username", "password"):
        tags = session.client("ssm").list_tags_for_resource(
            ResourceType="Parameter", ResourceId=prefix + "/" + suffix)["TagList"]
        assert {"Key": "auto-delete", "Value": "no"} in tags
