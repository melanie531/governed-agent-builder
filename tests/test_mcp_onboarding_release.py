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
    for role, name in (("WorkerRole", "McpOnboarding"), ("BusinessRole", "McpRegistryRead")):
        proposed["Resources"][role]["Properties"]["Policies"].append({"PolicyName": name})
    return previous, proposed


def test_existing_ui_release_only_adds_the_two_onboarding_policies():
    previous, proposed = fixture()
    review_existing_ui_change(previous, proposed)


def test_policy_order_does_not_change_iam_permissions():
    previous, proposed = fixture()
    for role in ("WorkerRole", "BusinessRole"):
        proposed["Resources"][role]["Properties"]["Policies"].reverse()
    review_existing_ui_change(previous, proposed)


def test_subsequent_release_updates_only_owned_onboarding_and_credential_policies():
    _, previous = fixture()
    proposed = copy.deepcopy(previous)
    proposed["Resources"]["WorkerRole"]["Properties"]["Policies"][1]["PolicyDocument"] = {"Statement": []}
    proposed["Resources"]["BusinessRole"]["Properties"]["Policies"].append({"PolicyName": "McpCredentialSetup"})
    review_existing_ui_change(previous, proposed)
    proposed["Resources"]["BusinessRole"]["Properties"]["Policies"][0]["PolicyDocument"] = {"Statement": []}
    with pytest.raises(ValueError):
        review_existing_ui_change(previous, proposed)


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
