import json
from types import SimpleNamespace

import pytest

from scripts import mcp_credentials_deploy as credentials
from scripts import mcp_onboarding_deploy as onboarding


def test_credential_cli_keeps_each_target_receipt_bound_to_its_own_stack(monkeypatch, tmp_path):
    class Target:
        def __init__(self, account, profile, region, state, **kwargs):
            self.state = json.loads(state.read_text())

        def save(self, key, value):
            self.state[key] = value

    monkeypatch.setattr(onboarding, "ROOT", tmp_path)
    monkeypatch.setattr(onboarding, "DeploymentTarget", Target)
    monkeypatch.setattr(onboarding.Release, "client", lambda self, name: SimpleNamespace(
        describe_stacks=lambda **kwargs: {"Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]}))
    original_prepare = credentials.prepare

    def prepare(release):
        original_prepare(release)
        release.save()

    monkeypatch.setattr(credentials, "prepare", prepare)
    saved = []
    for account, name in (("111111111111", "mcp-generic-ui"), ("222222222222", "burner-mcp")):
        state = tmp_path / f"{account}.json"
        stack = f"arn:aws:cloudformation:us-west-2:{account}:stack/app/receipt"
        state.write_text(json.dumps({
            "app": {"stackId": stack}, "journeyPlatform": {"mcp_onboarding": {}}}))
        arguments = ["prepare", "--expected-account", account, "--profile", "test",
                     "--region", "us-west-2", "--state", str(state)]
        if name != "mcp-generic-ui":
            arguments += ["--evidence-name", name]
        credentials.main(arguments)
        receipt = tmp_path / "artifacts" / name / "release-receipt.json"
        assert json.loads(receipt.read_text())["stack_id"] == stack
        saved.append((receipt, receipt.read_bytes()))
    assert all(path.read_bytes() == body for path, body in saved)


@pytest.mark.parametrize("name", ["../default", "/tmp/shared", "nested/path"])
def test_credential_cli_rejects_receipt_paths_outside_artifacts(monkeypatch, name):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid evidence path reached AWS target construction")

    monkeypatch.setattr(credentials, "Release", forbidden)
    with pytest.raises(SystemExit) as error:
        credentials.main(["prepare", "--expected-account", "222222222222", "--profile", "test",
                          "--region", "us-west-2", "--state", "state.json", "--evidence-name", name])
    assert error.value.code == 2


def test_verified_gateway_noop_can_complete_initial_setup(tmp_path):
    from infra.journey import template
    from infra.mcp_onboarding import configure_gateway
    settings = {"account": "123456789012", "region": "us-west-2", "gateway_id": "gab-journey-tools-test",
                "mcp_onboarding": {"credential_prefix": credentials.PREFIX}}
    body = template()
    configure_gateway(body["Resources"], settings)
    stack_id = "arn:aws:cloudformation:us-west-2:123456789012:stack/governed-agent-builder-journey/test"
    cloud = SimpleNamespace(
        describe_stacks=lambda **kwargs: {"Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]},
        get_template=lambda **kwargs: {"TemplateBody": body})
    saved = []
    release = SimpleNamespace(state={"journeyStack": {"id": stack_id}, "journeyPlatform": settings},
                              evidence=tmp_path, receipt={}, client=lambda name: cloud,
                              save=lambda: saved.append(True))
    credentials.gateway(release)
    assert release.receipt["gateway_verified"] is True
    assert saved == [True]
