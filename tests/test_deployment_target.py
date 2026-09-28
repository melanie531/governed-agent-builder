"""No SDK sessions or network: explicit target guards use synthetic stubs."""
import copy
import importlib
import json
from types import SimpleNamespace

import boto3
from botocore.exceptions import ClientError
import pytest

from scripts.deployment_target import DeploymentTarget, PREFIX

ACCOUNT = "9988" "77665544"
OTHER = "8877" "66554433"
REGION = "us-west-2"


def stack(suffix, account=ACCOUNT, region=REGION):
    outputs = {"Bucket": "synthetic-releases"} if suffix == "artifacts" else {"UserPoolId": "synthetic-pool", "ApplicationOrigin": "https://synthetic.example.test"}
    return {"StackId": f"arn:aws:cloudformation:{region}:{account}:stack/{PREFIX}-{suffix}/synthetic", "StackStatus": "CREATE_COMPLETE",
            "Tags": [{"Key": "project", "Value": "governed-agent-builder"}, {"Key": "architecture", "Value": "managed-serverless"}],
            "Outputs": [{"OutputKey": k, "OutputValue": v} for k, v in outputs.items()],
            "Parameters": [{"ParameterKey": "ArtifactBucket", "ParameterValue": "synthetic-releases"}]}


class SDK:
    def __init__(self, account=ACCOUNT, stacks=None):
        self.account, self.stacks, self.calls = account, stacks or {}, []

    def factory(self, **kwargs):
        assert kwargs == {"profile_name": "approved-test", "region_name": REGION}
        return self

    def client(self, service):
        self.calls.append(service)
        if service == "sts":
            return SimpleNamespace(get_caller_identity=lambda: {"Account": self.account})
        assert service == "cloudformation", "No write-capable service allowed in target validation"
        return self

    def describe_stacks(self, StackName):
        suffix = StackName.removeprefix(PREFIX + "-")
        if suffix not in self.stacks:
            raise ClientError({"Error": {"Code": "ValidationError", "Message": "Stack does not exist"}}, "DescribeStacks")
        return {"Stacks": [self.stacks[suffix]]}


def bound_file(path, stacks):
    state = {"target": {"account": ACCOUNT, "profile": "approved-test", "region": REGION}}
    for suffix, body in stacks.items():
        state[suffix] = {"stackId": body["StackId"], "outputs": {o["OutputKey"]: o["OutputValue"] for o in body["Outputs"]}}
    path.write_text(json.dumps(state))
    return state


def test_fresh_explicit_target_and_private_state(tmp_path):
    sdk = SDK()
    path = tmp_path / "fresh.json"
    target = DeploymentTarget(ACCOUNT, "approved-test", REGION, path, sdk.factory)
    assert sdk.calls[0] == "sts"
    assert not path.exists()
    target.save("checked", True)
    assert json.loads(path.read_text())["target"]["account"] == ACCOUNT
    assert path.stat().st_mode & 0o777 == 0o600


def test_wrong_sts_stops_before_cloudformation(tmp_path):
    sdk = SDK(OTHER)
    with pytest.raises(RuntimeError, match="STS account mismatch"):
        DeploymentTarget(ACCOUNT, "approved-test", REGION, tmp_path / "fresh.json", sdk.factory)
    assert sdk.calls == ["sts"]


@pytest.mark.parametrize("kind", ["legacy", "account", "profile", "region", "legacy-path"])
def test_bad_local_state_fails_before_sdk(tmp_path, kind):
    path = tmp_path / ("serverless-deployment.json" if kind == "legacy-path" else "fresh.json")
    state = bound_file(path, {})
    if kind == "legacy": state.pop("target")
    elif kind != "legacy-path": state["target"][kind] = "wrong"
    path.write_text(json.dumps(state))
    def forbidden(**kwargs): raise AssertionError("SDK must not be constructed")
    with pytest.raises(RuntimeError):
        DeploymentTarget(ACCOUNT, "approved-test", REGION, path, forbidden)


@pytest.mark.parametrize("kind", ["account", "region", "outputs", "stackid", "tags", "missing", "unbound", "bucket", "unstable", "output-arn"])
def test_inconsistent_live_stack_fails_closed(tmp_path, kind):
    stacks = {s: stack(s) for s in ("artifacts", "app")}
    path = tmp_path / "fresh.json"
    bound_file(path, {} if kind == "unbound" else stacks)
    live = copy.deepcopy(stacks)
    if kind == "account": live["app"] = stack("app", account=OTHER)
    elif kind == "region": live["app"] = stack("app", region="us-east-1")
    elif kind == "outputs": live["app"]["Outputs"][0]["OutputValue"] = "different-pool"
    elif kind == "stackid": live["app"]["StackId"] += "different"
    elif kind == "tags": live["app"]["Tags"] = []
    elif kind == "missing": del live["app"]
    elif kind == "bucket": live["app"]["Parameters"][0]["ParameterValue"] = "wrong-bucket"
    elif kind == "unstable": live["app"]["StackStatus"] = "UPDATE_IN_PROGRESS"
    elif kind == "output-arn": live["app"]["Outputs"][0]["OutputValue"] = f"arn:aws:cognito-idp:{REGION}:{OTHER}:userpool/synthetic"
    with pytest.raises(RuntimeError):
        DeploymentTarget(ACCOUNT, "approved-test", REGION, path, SDK(stacks=live).factory)


def test_matching_stacks_are_accepted(tmp_path):
    stacks = {s: stack(s) for s in ("artifacts", "app")}
    path = tmp_path / "fresh.json"
    bound_file(path, stacks)
    DeploymentTarget(ACCOUNT, "approved-test", REGION, path, SDK(stacks=stacks).factory)


def test_completed_rollback_requires_the_exact_reconciled_stack(tmp_path):
    stacks = {s: stack(s) for s in ("artifacts", "app")}
    stacks["app"]["StackStatus"] = "UPDATE_ROLLBACK_COMPLETE"
    path = tmp_path / "fresh.json"
    bound_file(path, stacks)
    with pytest.raises(RuntimeError):
        DeploymentTarget(ACCOUNT, "approved-test", REGION, path, SDK(stacks=stacks).factory)
    DeploymentTarget(ACCOUNT, "approved-test", REGION, path, SDK(stacks=stacks).factory,
                     reconciled_rollback_stack_id=stacks["app"]["StackId"])
    with pytest.raises(RuntimeError):
        DeploymentTarget(ACCOUNT, "approved-test", REGION, path, SDK(stacks=stacks).factory,
                         reconciled_rollback_stack_id=stacks["artifacts"]["StackId"])


def test_imports_and_legacy_entrypoints_never_construct_sdk(monkeypatch):
    def forbidden(*a, **kw): raise AssertionError("Unexpected SDK construction")
    monkeypatch.setattr(boto3, "Session", forbidden)
    for name in ("serverless_deploy", "cloud_deploy", "cloud_verify"):
        module = importlib.reload(importlib.import_module("scripts." + name))
        with pytest.raises(RuntimeError): module.main("publish")


def test_cli_requires_all_target_inputs():
    import argparse
    from scripts.deployment_target import target_arguments
    parser = argparse.ArgumentParser()
    target_arguments(parser)
    with pytest.raises(SystemExit): parser.parse_args([])
    args = parser.parse_args(["--expected-account", ACCOUNT, "--profile", "default", "--region", REGION, "--state", "fresh.json"])
    assert args.profile == "default"  # allowed only when operator explicitly selects it


@pytest.mark.parametrize('action', ['preflight', 'artifacts', 'deploy', 'publish', 'status'])
def test_every_action_checks_target_before_service_use(monkeypatch, action):
    from scripts import serverless_deploy as deploy
    def reject(): raise RuntimeError('synthetic target mismatch')
    class NoAWS:
        def __getattr__(self, name): raise AssertionError('AWS operation before guard')
    monkeypatch.setattr(deploy, 'TARGET', SimpleNamespace(check_stacks=reject))
    monkeypatch.setattr(deploy, 'SESSION', NoAWS())
    monkeypatch.setattr(deploy, 'CF', NoAWS())
    with pytest.raises(RuntimeError, match='synthetic target mismatch'):
        deploy.main(action)


def test_redeployment_preflight_preserves_enabled_journey_and_mcp(monkeypatch, tmp_path):
    from backend.store import Store
    from infra.serverless import template
    from scripts import serverless_deploy as release
    from tests.journey_support import seed
    from tests.test_mcp_onboarding_infra import settings as mcp_settings

    settings = seed(Store(str(tmp_path / "state.sqlite")))
    settings.update(mcp_settings(), evaluator_id="Builtin.Correctness",
                    evaluator_arn="arn:aws:bedrock-agentcore:::evaluator/Builtin.Correctness")
    expected = template(journey=settings)
    validated = []
    state = {"journeyPlatform": settings, "app": {"stackId": "bound-stack"}}
    monkeypatch.setattr(release, "TARGET", SimpleNamespace(
        state=state, check_stacks=lambda: None, save=lambda *args: None))
    monkeypatch.setattr(release, "CF", SimpleNamespace(
        get_template=lambda **kwargs: {"TemplateBody": expected},
        validate_template=lambda **kwargs: validated.append(json.loads(kwargs["TemplateBody"]))))

    release.preflight()

    assert validated[-1] == expected
    assert "agent-registry:CreateRegistryRecord" in json.dumps(validated[-1])
    assert validated[-1]["Resources"]["Worker"]["Properties"]["Environment"]["Variables"]["JOURNEY_ENABLED"] == "1"


def test_incomplete_deployment_state_cannot_remove_live_configuration(monkeypatch):
    from infra.serverless import template
    from scripts import serverless_deploy as release

    live = template()
    live["Resources"]["Business"]["Properties"]["Environment"]["Variables"]["JOURNEY_ENABLED"] = "1"
    def no_write(*args, **kwargs):
        raise AssertionError("Configuration loss must fail before validation or state writes")
    monkeypatch.setattr(release, "TARGET", SimpleNamespace(
        state={"app": {"stackId": "bound-stack"}}, check_stacks=lambda: None, save=no_write))
    monkeypatch.setattr(release, "CF", SimpleNamespace(
        get_template=lambda **kwargs: {"TemplateBody": live}, validate_template=no_write))

    with pytest.raises(RuntimeError, match="Live application template differs"):
        release.preflight()
