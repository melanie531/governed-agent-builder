"""Existing installations keep their legacy Cognito pool owner tag across upgrades.

Earlier installed templates tagged the user pool with a personal ``owner``
entry. The provider-neutral rendering no longer emits it, and CloudFormation
would strip the live tag on update. Upgrades must retain exactly that one
value from the verified live template; fresh installations must never gain
an owner tag, and every other template difference must still be rejected.
"""
import copy
import json
from types import SimpleNamespace

import pytest

from infra.resource_tags import retain_legacy_pool_owner_tag
from infra.serverless import template
from scripts import serverless_deploy as release

OWNER = "legacy-installer@example.invalid"


def legacy_live_template():
    live = template()
    live["Resources"]["Pool"]["Properties"]["UserPoolTags"]["owner"] = OWNER
    return live


def bind(monkeypatch, live, state=None):
    validated, requests, uploads = [], [], []
    stack = {"StackId": "bound-stack", "StackStatus": "UPDATE_COMPLETE",
             "Tags": [{"Key": "architecture", "Value": "managed-serverless"}],
             "Outputs": [{"OutputKey": "StateTable", "OutputValue": "synthetic-table"}]}
    monkeypatch.setattr(release, "TARGET", SimpleNamespace(
        state=state or {"app": {"stackId": "bound-stack"},
                        "artifacts": {"outputs": {"Bucket": "synthetic-releases"}}},
        check_stacks=lambda: None, check_stack=lambda _: None, save=lambda *args: None,
        binding={"region": "us-west-2"}))
    monkeypatch.setattr(release, "CF", SimpleNamespace(
        get_template=lambda **kwargs: {"TemplateBody": copy.deepcopy(live)},
        validate_template=lambda **kwargs: validated.append(json.loads(kwargs["TemplateBody"])),
        describe_stacks=lambda **kwargs: {"Stacks": [copy.deepcopy(stack)]},
        update_stack=lambda **kwargs: requests.append(kwargs),
        create_stack=lambda **kwargs: requests.append(kwargs)))
    monkeypatch.setattr(release, "SESSION", SimpleNamespace(
        client=lambda service: SimpleNamespace(
            upload_file=lambda *args, **kwargs: uploads.append(args),
            put_object=lambda **kwargs: {"VersionId": "test-version"}),
        resource=lambda service: None))
    return validated, requests, uploads


def test_preflight_accepts_legacy_owner_tag_and_validates_retained_rendering(monkeypatch):
    live = legacy_live_template()
    validated, _, _ = bind(monkeypatch, live)
    release.preflight()
    retained = validated[-1]
    assert retained["Resources"]["Pool"]["Properties"]["UserPoolTags"]["owner"] == OWNER
    without = copy.deepcopy(retained)
    del without["Resources"]["Pool"]["Properties"]["UserPoolTags"]["owner"]
    assert without == template()


def test_actual_deploy_submits_the_same_retained_owner_tag(monkeypatch, tmp_path):
    live = legacy_live_template()
    _, requests, uploads = bind(monkeypatch, live)
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts/serverless-release.zip").write_bytes(b"synthetic-release")
    monkeypatch.setattr(release, "ROOT", tmp_path)
    import backend.dynamo_store
    monkeypatch.setattr(backend.dynamo_store, "DynamoStore",
                        lambda *args, **kwargs: SimpleNamespace(initialize=lambda: None))
    release.main("deploy")
    assert len(requests) == 1 and uploads
    submitted = json.loads(requests[0]["TemplateBody"])
    assert submitted["Resources"]["Pool"]["Properties"]["UserPoolTags"]["owner"] == OWNER


def test_fresh_install_rendering_never_gains_an_owner_tag(monkeypatch):
    assert "owner" not in template()["Resources"]["Pool"]["Properties"]["UserPoolTags"]
    validated, _, _ = bind(monkeypatch, template(), state={
        "artifacts": {"outputs": {"Bucket": "synthetic-releases"}}})
    release.preflight()
    assert "owner" not in validated[-1]["Resources"]["Pool"]["Properties"]["UserPoolTags"]


@pytest.mark.parametrize("mutate", [
    lambda live: live["Resources"]["Pool"]["Properties"]["Policies"]["PasswordPolicy"].update(MinimumLength=6),
    lambda live: live["Resources"]["WorkerRole"]["Properties"]["Policies"].pop(),
    lambda live: live["Resources"]["Pool"]["Properties"].update(DeletionProtection="INACTIVE"),
    lambda live: live["Resources"]["Pool"]["Properties"]["UserPoolTags"].pop("auto-delete"),
])
def test_unrelated_cognito_security_iam_drift_is_still_rejected(monkeypatch, mutate):
    live = legacy_live_template()
    mutate(live)
    bind(monkeypatch, live)
    with pytest.raises(RuntimeError, match="Live application template differs"):
        release.preflight()


def test_retention_copies_only_a_verified_string_owner_value():
    proposed = template()
    assert retain_legacy_pool_owner_tag(legacy_live_template(), proposed)[
        "Resources"]["Pool"]["Properties"]["UserPoolTags"]["owner"] == OWNER
    # Unchanged proposed object: the helper must not mutate its input.
    assert "owner" not in proposed["Resources"]["Pool"]["Properties"]["UserPoolTags"]
    # No owner in the live template: nothing is added.
    assert retain_legacy_pool_owner_tag(template(), template()) == template()
    # Non-string live values are compatibility noise, never adopted.
    weird = legacy_live_template()
    weird["Resources"]["Pool"]["Properties"]["UserPoolTags"]["owner"] = {"Ref": "AWS::AccountId"}
    assert retain_legacy_pool_owner_tag(weird, template()) == template()
    # Only the pool owner tag is read; other live drift is never copied.
    drift = legacy_live_template()
    drift["Resources"]["Pool"]["Properties"]["MfaConfiguration"] = "OFF"
    retained = retain_legacy_pool_owner_tag(drift, template())
    assert retained["Resources"]["Pool"]["Properties"]["MfaConfiguration"] == "OPTIONAL"


def test_mcp_release_review_accepts_retained_owner_and_rejects_other_drift():
    from scripts.mcp_onboarding_deploy import review_existing_ui_change
    previous = {"Resources": {
        "Pool": {"Type": "AWS::Cognito::UserPool",
                 "Properties": {"UserPoolTags": {"auto-delete": "no", "owner": OWNER}}},
        "WorkerRole": {"Properties": {"Policies": [{"PolicyName": "Existing"}]}},
        "BusinessRole": {"Properties": {"Policies": [{"PolicyName": "Existing"}]}},
    }, "Outputs": {"ApplicationOrigin": {"Value": "existing"}}}
    proposed = copy.deepcopy(previous)
    del proposed["Resources"]["Pool"]["Properties"]["UserPoolTags"]["owner"]
    proposed["Resources"]["BusinessRole"]["Properties"]["Policies"].append(
        {"PolicyName": "McpRegistryRead"})
    proposed["Resources"]["McpOnboardingPolicy"] = {"Type": "AWS::IAM::ManagedPolicy", "Properties": {
        "Description": "Scoped onboarding", "Roles": [{"Ref": "WorkerRole"}],
        "PolicyDocument": {"Version": "2012-10-17", "Statement": []}}}
    with pytest.raises(ValueError, match="Unrelated existing application change"):
        review_existing_ui_change(previous, proposed)
    retained = retain_legacy_pool_owner_tag(previous, proposed)
    assert retained["Resources"]["Pool"]["Properties"]["UserPoolTags"]["owner"] == OWNER
    review_existing_ui_change(previous, retained)
    hostile = copy.deepcopy(retained)
    hostile["Resources"]["Pool"]["Properties"]["UserPoolTags"]["auto-delete"] = "yes"
    with pytest.raises(ValueError, match="Unrelated existing application change"):
        review_existing_ui_change(previous, hostile)
