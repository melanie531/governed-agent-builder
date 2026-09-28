"""CLI publication must use web-object uploads, not runtime artifact admission."""
from types import SimpleNamespace

import boto3
from moto import mock_aws

from scripts import bootstrap_support, journey_platform, serverless_deploy


def test_publish_ui_updates_existing_web_assets(monkeypatch, tmp_path):
    with mock_aws():
        session = boto3.Session(region_name="us-east-1", aws_access_key_id="testing",
                                aws_secret_access_key="testing")
        s3 = session.client("s3")
        s3.create_bucket(Bucket="test-studio-web")
        s3.put_bucket_versioning(Bucket="test-studio-web", VersioningConfiguration={"Status": "Enabled"})
        s3.put_object(Bucket="test-studio-web", Key="index.html", Body=b"previous")
        dist = tmp_path / "frontend/dist"
        dist.mkdir(parents=True)
        (dist / "index.html").write_text("current")
        cloudfront = SimpleNamespace(
            create_invalidation=lambda **kwargs: {"Invalidation": {"Id": "test-invalidation"}},
            get_invalidation=lambda **kwargs: {"Invalidation": {"Status": "Completed"}})
        state = {"app": {"outputs": {"FrontendBucket": "test-studio-web", "DistributionId": "test",
                                    "ApplicationOrigin": "https://studio.example.test"}}}
        target = SimpleNamespace(
            session=SimpleNamespace(client=lambda service, **kwargs: cloudfront if service == "cloudfront"
                                    else session.client(service, **kwargs)),
            state=state, path=tmp_path / "state.json", binding={"account": "123456789012"},
            check_stacks=lambda: None, save=lambda key, value: state.update({key: value}))
        target.cf = session.client("cloudformation")
        monkeypatch.setattr(bootstrap_support, "PlatformTarget", lambda *args: target)
        monkeypatch.setattr(serverless_deploy, "ROOT", tmp_path)
        for field in ("TARGET", "SESSION", "CF", "STATE"):
            monkeypatch.setattr(serverless_deploy, field, None)
        monkeypatch.setattr(journey_platform.sys, "argv", [
            "journey_platform", "publish-ui", "--expected-account", "123456789012",
            "--profile", "test", "--region", "us-east-1", "--state", str(target.path)])
        journey_platform.main()
        assert s3.get_object(Bucket="test-studio-web", Key="index.html")["Body"].read() == b"current"
        assert state["frontendPublication"]["invalidationId"] == "test-invalidation"
