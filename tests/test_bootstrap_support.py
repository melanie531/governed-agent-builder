"""Provider-neutral deployment durability and immutable artifact contracts."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from botocore.config import Config

from scripts import bootstrap_support as bootstrap
from scripts import journey_platform
from tests.bootstrap_support import BINDING, CloudFormation, Control, OUTPUTS, S3, Target


def test_journal_records_intent_before_write_and_never_replays_unknown():
    target, writes = Target(), []
    journal = bootstrap.Journal(target)
    def write(token):
        assert target.state["platformOperations"]["create"]["request_token"] == token
        writes.append(token)
        raise TimeoutError("synthetic sensitive error must not persist")
    for _ in range(2):
        with pytest.raises(bootstrap.PendingOperation):
            journal.run("create", {"name": "owned"}, write, lambda record: None)
    assert len(writes) == 1
    assert "sensitive" not in json.dumps(target.state)
    result = journal.run("create", {"name": "owned"}, write, lambda record: {"id": "recovered"})
    assert result == {"id": "recovered"} and len(writes) == 1
    with pytest.raises(RuntimeError, match="changed"):
        journal.run("create", {"name": "different"}, write, lambda record: None)


def test_sdk_retry_policy_overrides_upload_helpers_retry_settings():
    calls = []
    raw = SimpleNamespace(client=lambda name, **kwargs: calls.append(kwargs) or SimpleNamespace(),
                          resource=lambda name, **kwargs: calls.append(kwargs) or SimpleNamespace())
    session = bootstrap.NoRetrySession(raw, Target())
    session.client("s3", config=Config(retries={"max_attempts": 9}, s3={"payload_signing_enabled": True}))
    session.resource("dynamodb")
    session.client("bedrock-agentcore-control")
    assert all(call["config"].retries["total_max_attempts"] == 1 for call in calls)
    assert calls[0]["config"].s3["payload_signing_enabled"] is True
    assert all(call["config"].parameter_validation is True for call in calls)


def test_durable_state_preserves_other_agent_updates(tmp_path):
    target = bootstrap.PlatformTarget.__new__(bootstrap.PlatformTarget)
    target.path = tmp_path / "state.json"
    target.binding, target.state = BINDING, {"target": BINDING, "app": {"initial": True}}
    target.path.write_text(json.dumps({**target.state, "releaseSha256": "other-agent"}))
    settings = {"enabled": True, "gateway_id": "test-gateway"}
    target.save("journeyPlatform", settings)
    assert json.loads(target.path.read_text())["releaseSha256"] == "other-agent"
    assert target.path.stat().st_mode & 0o777 == 0o600
    changed = {**target.state, "journeyPlatform": {"concurrent": True}}
    target.path.write_text(json.dumps(changed))
    with pytest.raises(RuntimeError, match="Concurrent"):
        target.save("journeyPlatform", settings)
    assert json.loads(target.path.read_text()) == changed


def test_base_lambda_upload_is_reused_only_after_versioned_content_verification(tmp_path):
    source = tmp_path / "lambda.zip"
    source.write_bytes(b"synthetic-lambda")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    target, s3 = Target(), S3(source.read_bytes())
    target.state.update(releaseSha256=sha, artifacts={"outputs": {"Bucket": "test-artifacts"}})
    s3.head = {"ContentLength": source.stat().st_size, "VersionId": "base-version",
               "ServerSideEncryption": "AES256", "Metadata": {}}
    client = bootstrap.UploadClient(s3, target)
    client.upload_file(str(source), "test-artifacts", f"releases/{sha}/lambda.zip",
                       ExtraArgs={"ServerSideEncryption": "AES256"}, Config=None)
    assert s3.writes == 0
    s3.data = b"tampered-release"
    with pytest.raises(RuntimeError):
        client.upload_file(str(source), "test-artifacts", f"releases/{sha}/lambda.zip",
                           ExtraArgs={"ServerSideEncryption": "AES256"}, Config=None)


@pytest.mark.parametrize("lost", [False, True])
def test_upload_reconciles_once_without_post_replay(tmp_path, lost):
    source = tmp_path / "runtime.zip"
    source.write_bytes(b"synthetic-runtime")
    target, s3 = Target(), S3()
    s3.lose = lost
    client = bootstrap.UploadClient(s3, target)
    for _ in range(2):
        client.upload_file(str(source), "test-artifacts", "journey/foundation/hash.zip",
                           ExtraArgs={"ServerSideEncryption": "AES256"}, Config=None)
    assert s3.writes == 1
    assert next(iter(target.state["platformOperations"].values()))["status"] == "COMPLETE"


@pytest.mark.parametrize("lost", [False, True])
def test_shared_stack_template_and_request_token_recovery(lost):
    target = Target()
    target.cf = CloudFormation(target)
    target.cf.lose = lost
    result = bootstrap.stack(target, "test-artifacts", "test-key")
    assert bootstrap.stack(target, "test-artifacts", "test-key") == result
    assert len(target.cf.writes) == 1
    assert target.cf.body == journey_platform.platform_template()
    target.cf.body["Description"] = "different deployment"
    with pytest.raises(RuntimeError, match="contract changed"):
        bootstrap.stack(target, "test-artifacts", "test-key")
    assert len(target.cf.writes) == 1


@pytest.mark.parametrize("field,value", [
    ("authorizerType", "CUSTOM_JWT"), ("roleArn", "arn:aws:iam::123456789012:role/different"),
])
def test_saved_gateway_binding_drift_stops_without_new_writes(field, value):
    target = Target()
    control = Control(target)
    bootstrap.gateway(target, control, OUTPUTS)
    before = list(control.calls)
    control.gw[field] = value
    with pytest.raises(RuntimeError):
        bootstrap.gateway(target, control, OUTPUTS)
    assert control.calls == before
