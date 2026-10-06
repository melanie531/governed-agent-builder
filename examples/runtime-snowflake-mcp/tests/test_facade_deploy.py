import json
from types import SimpleNamespace

import pytest

import facade_deploy as module


def python_config():
    return {
        "prefix": "studio-python", "profile": "test", "account": "123456789012", "region": "us-east-1",
        "bundle_name": "Snowflake MCP", "bundle_digest": "a" * 64,
        "python_onboarding": {
            "table_name": "studio-state", "runtime_prefix": "studio_python",
            "deployment_prefix": "test-studio", "worker_role_name": "studio-worker",
            "artifact": {"bucket": "studio-private-releases", "key": "mcp/python/base.zip", "version_id": "v1"},
        },
    }


def test_python_facade_installation_preserves_the_explicit_studio_and_bundle_binding(tmp_path, monkeypatch):
    source = tmp_path / "python-config.json"
    source.write_text(json.dumps(python_config()))
    monkeypatch.setattr(module.boto3, "Session", lambda **_: SimpleNamespace(
        client=lambda *a, **kw: SimpleNamespace(get_caller_identity=lambda: {"Account": "123456789012"})))
    deployment = module.FacadeDeployment(None, tmp_path / "release", python_config=source)
    assert deployment.config["artifact_bucket"] == "studio-private-releases"
    assert deployment.body["Resources"]["Route"]["Properties"]["RouteKey"] == "ANY /mcp/{python_id}"
    assert deployment.state["config"]["python_onboarding"] == python_config()["python_onboarding"]
    changed = python_config()
    changed["python_onboarding"]["worker_role_name"] = "another-worker"
    source.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="binding"):
        module.FacadeDeployment(None, tmp_path / "release", python_config=source)


@pytest.mark.parametrize("reserved_concurrency", [None, 2])
def test_python_hosting_can_explicitly_fit_the_target_lambda_quota(tmp_path, monkeypatch, reserved_concurrency):
    config = {**python_config(), "reserved_concurrency": reserved_concurrency}
    source = tmp_path / "python-config.json"
    source.write_text(json.dumps(config))
    monkeypatch.setattr(module.boto3, "Session", lambda **_: SimpleNamespace(
        client=lambda *a, **kw: SimpleNamespace(get_caller_identity=lambda: {"Account": "123456789012"})))
    deployment = module.FacadeDeployment(None, tmp_path / "release", python_config=source)
    functions = [r["Properties"] for r in deployment.body["Resources"].values()
                 if r["Type"] == "AWS::Lambda::Function"]
    assert len(functions) == 2
    for properties in functions:
        if reserved_concurrency is None:
            assert "ReservedConcurrentExecutions" not in properties
        else:
            assert properties["ReservedConcurrentExecutions"] == reserved_concurrency
    assert deployment.state["config"]["reserved_concurrency"] == reserved_concurrency


def tagging_deployment():
    deployment = module.FacadeDeployment.__new__(module.FacadeDeployment)
    deployment.config = python_config()
    deployment.state = {"operations": {}, "facade": {"outputs": {
        "DeploymentPolicyArn": "arn:aws:iam::123456789012:policy/studio-python-deployment"}}}
    deployment.save = lambda: None
    return deployment


def test_policy_tagging_journals_before_mutation_and_reconciles_an_acknowledgment_loss():
    deployment = tagging_deployment()
    tags, writes = {}, []
    def tag(**request):
        assert deployment.state["operations"]["policy-tags"]["phase"] == "INTENT"
        writes.append(request)
        tags.update({t["Key"]: t["Value"] for t in request["Tags"]})
        raise TimeoutError("lost acknowledgment")
    deployment.client = lambda _: SimpleNamespace(
        list_policy_tags=lambda **_: {"Tags": [{"Key": k, "Value": v} for k, v in tags.items()]},
        tag_policy=tag)
    with pytest.raises(TimeoutError):
        deployment.tag_python_policy()
    deployment.tag_python_policy()
    assert len(writes) == 1
    assert tags == {"auto-delete": "no", "project": "governed-agent-builder", "deployment": "test-studio"}
    assert deployment.state["operations"]["policy-tags"]["phase"] == "VERIFIED"


def test_missing_policy_tags_after_uncertain_submission_do_not_automatically_repeat_the_write():
    deployment = tagging_deployment()
    writes = []
    def tag(**request):
        writes.append(request)
        raise TimeoutError("request outcome unknown")
    deployment.client = lambda _: SimpleNamespace(list_policy_tags=lambda **_: {"Tags": []}, tag_policy=tag)
    with pytest.raises(TimeoutError):
        deployment.tag_python_policy()
    with pytest.raises(RuntimeError, match="not repeated"):
        deployment.tag_python_policy()
    assert len(writes) == 1


def test_facade_update_requires_an_explicit_release_and_keeps_the_previous_receipt(tmp_path):
    deployment = tagging_deployment()
    deployment.directory = tmp_path
    deployment.config["artifact_bucket"] = "studio-private-releases"
    deployment.body = {"Resources": {}}
    deployment.state.update(upload={"digest": "0" * 64, "bucket": "studio-private-releases",
                                    "key": "old.zip", "version": "old-v1"})
    deployment.state["facade"].update(id="owned-stack")
    deployment.state["operations"]["facade"] = {"digest": "old-intent", "phase": "CREATE_COMPLETE"}
    calls = []
    deployment.plan = lambda: calls.append("plan")
    deployment.audit = lambda: calls.append("audit")
    deployment.tag_python_policy = lambda: None
    def upload(package, bucket, sha):
        assert deployment.state["release_history"][-1]["upload"]["version"] == "old-v1"
        assert "facade" not in deployment.state["operations"]
        deployment.state["upload"] = {"bucket": bucket, "key": "new.zip", "version": "new-v1", "digest": sha}
        return deployment.state["upload"]
    def stack(section, body, parameters, **kwargs):
        assert section == "facade" and kwargs == {"update": True}
        calls.append("update")
        return {"McpEndpoint": "https://existing.example.com/mcp"}
    deployment.upload_package, deployment.stack = upload, stack
    with pytest.raises(ValueError, match="new release"):
        deployment.deploy()
    assert calls == ["plan"]
    deployment.deploy(new_release=True)
    assert calls == ["plan", "plan", "audit", "update", "audit"]
    assert deployment.state["release_history"][0]["facade"]["id"] == "owned-stack"
