"""The fresh-account CLI owns setup; operators never edit platform records."""
import copy
import json
from pathlib import Path
import subprocess

import pytest

from scripts import studio_install as install


def configuration(tmp_path, **changes):
    path = tmp_path / "install.json"
    path.write_text(json.dumps({
        "account": "123456789012", "profile": "customer-profile", "region": "us-west-2",
        **changes,
    }))
    return install.load_configuration(path, root=tmp_path)


def test_configuration_derives_target_paths_without_personal_environment(tmp_path):
    config = configuration(tmp_path)
    assert config.binding == {
        "account": "123456789012", "profile": "customer-profile", "region": "us-west-2"}
    assert config.worker_memory_size == 512
    assert config.package_reserved_concurrency is None
    assert config.user_oauth and config.package_uploads
    assert config.state == tmp_path / "artifacts/account-123456789012-us-west-2/release-state.json"
    assert config.evidence_name == "install-123456789012-us-west-2"
    assert "tag" not in json.dumps(config.intent()).lower()


@pytest.mark.parametrize("changes", [
    {"account": 123456789012}, {"region": "us-gov-west-1"},
    {"profile": ""}, {"worker_memory_size": True}, {"worker_memory_size": 256},
    {"package_reserved_concurrency": 0}, {"package_uploads": "true"},
    {"user_oauth": None}, {"password": "never-store"},
    {"evidence_name": "../another-installation"},
])
def test_invalid_or_unrelated_configuration_is_rejected(tmp_path, changes):
    with pytest.raises(ValueError):
        configuration(tmp_path, **changes)


def test_duplicate_configuration_keys_are_rejected(tmp_path):
    path = tmp_path / "install.json"
    path.write_text('{"account":"123456789012","account":"999999999999"}')
    with pytest.raises(ValueError, match="Duplicate"):
        install.load_configuration(path, root=tmp_path)


def test_pipeline_contains_every_required_stage_and_only_selected_addons(tmp_path):
    full = install.stage_names(configuration(tmp_path))
    assert full == (
        "preflight", "build", "artifacts", "application", "runtime-prerequisites",
        "platform", "registry", "credentials", "user-oauth", "package-hosting",
        "activate", "publish", "audit",
    )
    minimal = install.stage_names(configuration(tmp_path, user_oauth=False, package_uploads=False))
    assert minimal == tuple(name for name in full if name not in ("user-oauth", "package-hosting"))


class State:
    def __init__(self):
        self.state = {"installation": {"steps": {}}}
        self.saves = []

    def save(self, key, value):
        self.state[key] = copy.deepcopy(value)
        self.saves.append(copy.deepcopy(self.state))


def test_interrupted_pipeline_stops_and_resumes_without_repeating_completed_stages():
    target, calls = State(), []
    lose_response = True

    def first():
        assert target.state["installation"]["steps"]["base"]["status"] == "RUNNING"
        calls.append("base")

    def second():
        nonlocal lose_response
        calls.append("gateway")
        if lose_response:
            lose_response = False
            raise TimeoutError("sensitive diagnostic must not enter the receipt")

    steps = {"base": first, "gateway": second, "audit": lambda: calls.append("audit")}
    with pytest.raises(TimeoutError):
        install.run_steps(target, steps)
    assert calls == ["base", "gateway"]
    saved = target.state["installation"]["steps"]
    assert saved["base"]["status"] == "COMPLETE"
    assert saved["gateway"]["status"] == "NEEDS_RECONCILIATION"
    assert "sensitive diagnostic" not in json.dumps(target.state)
    install.run_steps(target, steps)
    assert calls == ["base", "gateway", "gateway", "audit"]
    assert all(item["status"] == "COMPLETE" for item in target.state["installation"]["steps"].values())


def test_completed_stage_is_not_skipped_after_artifact_drift():
    target = State()
    target.state["installation"]["steps"]["build"] = {"status": "COMPLETE"}
    dispatched = []

    def verify(name):
        if name == "build":
            raise ValueError("Pinned artifact changed")

    with pytest.raises(ValueError, match="Pinned artifact"):
        install.run_steps(target, {"build": lambda: None, "app": lambda: dispatched.append(True)},
                          verify_completed=verify)
    assert not dispatched


def test_existing_personal_owner_is_not_part_of_new_identity_template():
    from infra.identity import template
    tags = template("https://studio.example.com")["Resources"]["Pool"]["Properties"]["UserPoolTags"]
    assert "owner" not in tags
    assert tags["project"] == "governed-agent-builder"


def test_package_hosting_configuration_is_derived_from_this_installation(tmp_path):
    config = configuration(tmp_path)
    artifact = {"bucket": "customer-artifacts", "key": "bundle.zip", "version": "v1", "digest": "a" * 64}
    state = {
        "app": {"outputs": {"StateTable": "customer-state"}},
        "journeyPlatform": {"mcp_onboarding": {"credential_prefix": "governed-agent-builder-serverless"}},
    }
    result = install.hosting_configuration(
        config, state, artifact, "arn:aws:iam::123456789012:role/customer-worker")
    assert result["python_onboarding"] == {
        "table_name": "customer-state", "worker_role_name": "customer-worker",
        "runtime_prefix": "studio_python_mcp", "deployment_prefix": "governed-agent-builder-serverless",
        "artifact": {"bucket": "customer-artifacts", "key": "bundle.zip", "version_id": "v1"},
    }
    assert result["reserved_concurrency"] is None
    assert result["bundle_digest"] == "a" * 64
    assert "snowflake" not in json.dumps(result).lower()
    with pytest.raises(ValueError, match="Worker"):
        install.hosting_configuration(config, state, artifact, "arn:aws:iam::999999999999:role/wrong-account")


def test_plan_is_local_and_requires_no_aws_session(tmp_path, monkeypatch, capsys):
    import boto3
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"account": "123456789012", "profile": "customer-profile", "region": "us-west-2"}))
    monkeypatch.setattr(boto3, "Session", lambda **_: pytest.fail("Plan must not create an AWS session"))
    install.main(["plan", "--config", str(path)])
    result = json.loads(capsys.readouterr().out)
    assert result["aws_writes"] is False
    assert result["stages"] == list(install.STAGES)
    assert result["user_acceptance"] == "NOT_RUN"


def test_hosting_installer_lock_contains_its_actual_backend_dependencies(monkeypatch):
    """Exercise the same isolated interpreter/imports as facade configure."""
    example = install.ROOT / "examples/runtime-snowflake-mcp"
    monkeypatch.setenv("PYTHONPATH", str(install.ROOT))
    subprocess.run(
        ["uv", "run", "--locked", "--python", "3.13", "--project", str(example),
         "--group", "studio-installer", "python", "-c",
         "import aws_cdk, backend.dynamo_store, backend.foundation_runs, backend.mcp_python_cloud"],
        cwd=install.ROOT, check=True,
        capture_output=True, text=True,
    )


def test_evidence_from_another_target_is_rejected_before_aws_or_build(tmp_path, monkeypatch):
    config = configuration(tmp_path, evidence_name="shared-release")
    config.evidence.mkdir(parents=True)
    (config.evidence / "installation-inputs.json").write_text(json.dumps({
        "configuration": {**config.intent(), "account": "999999999999"},
        "source_sha256": install.source_digest(tmp_path),
    }))
    monkeypatch.setattr(install, "InstallTarget", lambda *args: pytest.fail("AWS target must not be opened"))
    with pytest.raises(RuntimeError, match="evidence"):
        install.Pipeline(config)


def test_unrecorded_evidence_directory_is_preserved(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    config.evidence.mkdir(parents=True)
    package = config.evidence / "lambda.zip"
    package.write_bytes(b"another release")
    monkeypatch.setattr(install, "InstallTarget", lambda *args: pytest.fail("AWS target must not be opened"))
    with pytest.raises(RuntimeError, match="evidence"):
        install.Pipeline(config)
    assert package.read_bytes() == b"another release"
