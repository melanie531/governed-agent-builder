import copy

import pytest

from scripts.mcp_onboarding_deploy import review_evaluated_changes, review_existing_ui_change


def fixture():
    previous = {"Resources": {
        "Pool": {"Type": "AWS::Cognito::UserPool", "Properties": {}},
        "WorkerRole": {"Properties": {"Policies": [{"PolicyName": "Existing"}]}},
        "BusinessRole": {"Properties": {"Policies": [{"PolicyName": "Existing"}]}},
    }, "Outputs": {"ApplicationOrigin": {"Value": "existing"}}}
    proposed = copy.deepcopy(previous)
    for role, name in (("BusinessRole", "McpRegistryRead"),):
        proposed["Resources"][role]["Properties"]["Policies"].append({"PolicyName": name})
    proposed["Resources"]["McpOnboardingPolicy"] = {"Type": "AWS::IAM::ManagedPolicy", "Properties": {
        "Description": "Scoped onboarding", "Roles": [{"Ref": "WorkerRole"}],
        "PolicyDocument": {"Version": "2012-10-17", "Statement": []}}}
    return previous, proposed


def test_existing_ui_release_only_adds_the_two_onboarding_policies():
    previous, proposed = fixture()
    review_existing_ui_change(previous, proposed)


def test_user_authorization_release_allows_its_scoped_business_policy_addition():
    previous, proposed = fixture()
    for role, name in (("BusinessRole", "McpUserAuthorization"),):
        proposed["Resources"][role]["Properties"]["Policies"].append({"PolicyName": name})
    review_existing_ui_change(previous, proposed)
    proposed["Resources"]["WorkerRole"]["Properties"]["Policies"].append({"PolicyName": "Unrelated"})
    with pytest.raises(ValueError, match="Unrelated"):
        review_existing_ui_change(previous, proposed)


def test_policy_order_does_not_change_iam_permissions():
    previous, proposed = fixture()
    for role in ("WorkerRole", "BusinessRole"):
        proposed["Resources"][role]["Properties"]["Policies"].reverse()
    review_existing_ui_change(previous, proposed)


def test_subsequent_release_updates_only_owned_onboarding_and_credential_policies():
    _, previous = fixture()
    proposed = copy.deepcopy(previous)
    proposed["Resources"]["McpOnboardingPolicy"]["Properties"]["PolicyDocument"] = {"Statement": []}
    proposed["Resources"]["BusinessRole"]["Properties"]["Policies"].append({"PolicyName": "McpCredentialSetup"})
    review_existing_ui_change(previous, proposed)
    proposed["Resources"]["BusinessRole"]["Properties"]["Policies"][0]["PolicyDocument"] = {"Statement": []}
    with pytest.raises(ValueError):
        review_existing_ui_change(previous, proposed)


def test_cleanup_can_remove_only_the_retired_worker_creation_policy():
    previous, proposed = fixture()
    previous["Resources"]["WorkerRole"]["Properties"]["Policies"].append({
        "PolicyName": "McpCreation", "PolicyDocument": {"Statement": [
            {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"], "Resource": "retired-creator-secret"}]}})
    original = copy.deepcopy(previous)
    review_existing_ui_change(previous, proposed)
    assert previous == original
    # The cleanup exception must not permit unrelated policy removal.
    proposed["Resources"]["WorkerRole"]["Properties"]["Policies"] = [
        p for p in proposed["Resources"]["WorkerRole"]["Properties"]["Policies"] if p["PolicyName"] != "Existing"]
    with pytest.raises(ValueError):
        review_existing_ui_change(previous, proposed)


def test_retired_creation_permissions_cannot_be_reintroduced():
    previous, proposed = fixture()
    proposed["Resources"]["WorkerRole"]["Properties"]["Policies"].append({"PolicyName": "McpCreation"})
    previous["Resources"]["WorkerRole"]["Properties"]["Policies"].append({"PolicyName": "McpCreation"})
    with pytest.raises(ValueError):
        review_existing_ui_change(previous, proposed)


def test_existing_inline_onboarding_policy_moves_to_only_the_worker_managed_policy():
    previous, proposed = fixture()
    previous["Resources"]["WorkerRole"]["Properties"]["Policies"].append({
        "PolicyName": "McpOnboarding", "PolicyDocument": {"Statement": []}})
    review_existing_ui_change(previous, proposed)
    proposed["Resources"]["McpOnboardingPolicy"]["Properties"]["Roles"].append({"Ref": "BusinessRole"})
    with pytest.raises(ValueError, match="worker-bound"):
        review_existing_ui_change(previous, proposed)


def test_evaluated_release_allows_only_the_declared_new_managed_policy():
    resource = {"LogicalResourceId": "McpOnboardingPolicy", "ResourceType": "AWS::IAM::ManagedPolicy", "Action": "Add"}
    assert review_evaluated_changes([{"ResourceChange": resource}], {})
    for changed in ({**resource, "LogicalResourceId": "UnrelatedPolicy"},
                    {**resource, "ResourceType": "AWS::IAM::Role"}):
        with pytest.raises(ValueError):
            review_evaluated_changes([{"ResourceChange": changed}], {})


def test_lambda_uri_dependency_is_allowed_but_unrelated_uri_change_is_not():
    change = {"ResourceChange": {"LogicalResourceId": "AuthIntegration", "Action": "Modify", "Replacement": "False",
        "Details": [{"ChangeSource": "ResourceAttribute", "CausingEntity": "Auth.Arn",
                     "Target": {"Name": "IntegrationUri", "RequiresRecreation": "Never"}}]}}
    review_evaluated_changes([change], {})
    change["ResourceChange"]["Details"][0]["CausingEntity"] = "Foreign.Arn"
    with pytest.raises(ValueError):
        review_evaluated_changes([change], {})


@pytest.mark.parametrize("change", ["pool", "output", "resource", "existing-policy"])
def test_existing_ui_release_rejects_unrelated_changes(change):
    previous, proposed = fixture()
    if change == "pool":
        proposed["Resources"]["Pool"]["Properties"]["UserPoolName"] = "replacement"
    elif change == "output":
        proposed["Outputs"]["ApplicationOrigin"]["Value"] = "new"
    elif change == "resource":
        proposed["Resources"]["AnotherApp"] = {}
    else:
        proposed["Resources"]["WorkerRole"]["Properties"]["Policies"][0]["PolicyName"] = "Changed"
    with pytest.raises(ValueError):
        review_existing_ui_change(previous, proposed)


def test_release_evidence_name_accepts_new_releases_but_cannot_escape_artifacts():
    import argparse
    from scripts.mcp_onboarding_deploy import evidence_name
    assert evidence_name("repository-cleanup-20260926") == "repository-cleanup-20260926"
    for value in ("../elsewhere", "/tmp/release", "", "release/child", "."):
        with pytest.raises(argparse.ArgumentTypeError):
            evidence_name(value)


def test_large_cloudformation_template_uses_a_private_versioned_s3_object():
    from types import SimpleNamespace
    from urllib.parse import parse_qs, urlsplit
    from scripts.mcp_onboarding_deploy import Release

    release = Release.__new__(Release)
    release.state = {"artifacts": {"outputs": {"Bucket": "private-releases"}}}
    release.target = SimpleNamespace(binding={"region": "us-east-1"})
    uploads = []
    release.upload = lambda key, content: uploads.append((key, content)) or "version+with/slash"
    assert release.template_input({"Resources": {}}) == {"TemplateBody": '{"Resources":{}}'}
    assert not uploads
    body = {"Description": "x" * 52000, "Resources": {}}
    request = release.template_input(body)
    assert set(request) == {"TemplateURL"}
    url = urlsplit(request["TemplateURL"])
    assert url.scheme == "https" and url.hostname == "private-releases.s3.us-east-1.amazonaws.com"
    assert parse_qs(url.query) == {"versionId": ["version+with/slash"]}
    assert url.path == "/" + uploads[0][0]
    assert __import__("json").loads(uploads[0][1]) == body


@pytest.mark.parametrize("matching_checksum", [True, False])
def test_immutable_release_artifact_is_reconciled_after_create_only_conflict(tmp_path, matching_checksum):
    import base64
    import hashlib

    import boto3
    from botocore.stub import Stubber
    from scripts.mcp_onboarding_deploy import Release

    content = b"retained immutable template"
    sha = hashlib.sha256(content).hexdigest()
    checksum = base64.b64encode(bytes.fromhex(sha)).decode()
    s3 = boto3.client("s3", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing")
    release = Release.__new__(Release)
    release.state = {"artifacts": {"outputs": {"Bucket": "private-releases"}}}
    release.receipt = {"operations": {}}
    release.path = tmp_path / "release-receipt.json"
    release.client = lambda _: s3
    with Stubber(s3) as stub:
        stub.add_client_error("put_object", service_error_code="PreconditionFailed", http_status_code=412,
            expected_params={"Bucket": "private-releases", "Key": "template.json", "Body": content,
                "ServerSideEncryption": "AES256", "Metadata": {"sha256": sha}, "IfNoneMatch": "*",
                "Tagging": "auto-delete=no", "ChecksumSHA256": checksum})
        stub.add_response("head_object", {
            "Metadata": {"sha256": sha}, "ChecksumSHA256": checksum if matching_checksum else "mismatch",
            "ServerSideEncryption": "AES256", "VersionId": "retained-version"},
            {"Bucket": "private-releases", "Key": "template.json", "ChecksumMode": "ENABLED"})
        stub.add_response("get_object_tagging", {"TagSet": [{"Key": "auto-delete", "Value": "no"}]},
            {"Bucket": "private-releases", "Key": "template.json", "VersionId": "retained-version"})
        if matching_checksum:
            assert release.upload("template.json", content) == "retained-version"
            assert release.receipt["operations"]["artifact-" + sha]["status"] == "VERIFIED"
        else:
            with pytest.raises(ValueError, match="artifact is not verified"):
                release.upload("template.json", content)
            assert release.receipt["operations"]["artifact-" + sha]["status"] == "INTENT"
        stub.assert_no_pending_responses()


def test_publication_uploads_index_last_and_waits_for_invalidation(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from scripts import mcp_onboarding_deploy as module
    dist = tmp_path / "frontend/dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("release index")
    (dist / "z-last.svg").write_text("<svg/>")
    (dist / "assets/app.js").write_text("release script")
    calls = []
    def invalidate(**kwargs):
        calls.append("invalidate")
        return {"Invalidation": {"Id": "test-invalidation"}}
    def read(**kwargs):
        calls.append("completed")
        return {"Invalidation": {"Status": "Completed"}}
    clients = {
        "s3": SimpleNamespace(put_object=lambda **kwargs: calls.append(kwargs["Key"])),
        "cloudfront": SimpleNamespace(create_invalidation=invalidate, get_invalidation=read),
    }
    release = module.Release.__new__(module.Release)
    release.state = {"app": {"outputs": {
        "FrontendBucket": "private-bucket", "DistributionId": "test-distribution",
        "ApplicationOrigin": "https://studio.example.test"}}}
    release.receipt = {"deployment_verified": True}
    release.client = clients.__getitem__
    release.save = lambda: None
    monkeypatch.setattr(module, "ROOT", tmp_path)

    release.publish()

    assert calls == ["assets/app.js", "z-last.svg", "index.html", "invalidate", "completed"]
    assert release.receipt["frontend"]["status"] == "Completed"
