"""Installer fixes retain the original application build and native receipts."""
import hashlib
import json
from types import SimpleNamespace

import boto3
from botocore.awsrequest import AWSResponse
from botocore.exceptions import ReadTimeoutError
import pytest

from foundation_harness.config import digest
from scripts import studio_install as install
from scripts import studio_install_support as support
from scripts.bootstrap_support import NoRetrySession, PlatformTarget


def test_deployment_clients_allow_tls_handshakes_without_retrying_writes():
    session = boto3.Session(region_name="us-west-2", aws_access_key_id="testing",
                            aws_secret_access_key="testing")
    client = NoRetrySession(session, SimpleNamespace()).client("cloudformation")
    assert client.meta.config.connect_timeout >= 30
    assert client.meta.config.retries["total_max_attempts"] == 1
    from scripts.mcp_onboarding_deploy import Release
    release = Release.__new__(Release)
    release.clients, release.target = {}, SimpleNamespace(session=session)
    assert release.client("cloudformation").meta.config.connect_timeout >= 30
    assert release.client("cloudformation").meta.config.retries["total_max_attempts"] == 1


@pytest.mark.parametrize("factory", ["bootstrap", "release"])
@pytest.mark.parametrize("operation,failures,expected_calls", [
    ("describe_stacks", 2, 3), ("describe_stacks", 4, 3), ("create_stack", 2, 1),
])
def test_transient_cloudformation_reads_retry_without_replaying_writes(
        monkeypatch, factory, operation, failures, expected_calls):
    """Run the real SDK retry/HTTP boundary, substituting only its transport."""
    session = boto3.Session(region_name="us-west-2", aws_access_key_id="testing",
                            aws_secret_access_key="testing")
    if factory == "bootstrap":
        client = NoRetrySession(session, SimpleNamespace()).client("cloudformation")
    else:
        from scripts.mcp_onboarding_deploy import Release
        release = Release.__new__(Release)
        release.clients, release.target = {}, SimpleNamespace(session=session)
        client = release.client("cloudformation")
    calls = []
    body = b'<DescribeStacksResponse xmlns="http://cloudformation.amazonaws.com/doc/2010-05-15/"><DescribeStacksResult><Stacks/></DescribeStacksResult><ResponseMetadata><RequestId>test-read</RequestId></ResponseMetadata></DescribeStacksResponse>'

    def send(request):
        calls.append(request)
        if len(calls) <= failures:
            raise ReadTimeoutError(endpoint_url=request.url)
        return AWSResponse(request.url, 200, {"content-type": "text/xml"},
                           SimpleNamespace(stream=lambda: iter([body])))

    monkeypatch.setattr(client._endpoint.http_session, "send", send)
    monkeypatch.setattr("botocore.endpoint.time.sleep", lambda _: None)
    if operation == "describe_stacks" and failures < expected_calls:
        assert client.describe_stacks()["Stacks"] == []
    else:
        with pytest.raises(ReadTimeoutError):
            if operation == "describe_stacks":
                client.describe_stacks()
            else:
                client.create_stack(StackName="test", TemplateBody='{"Resources":{}}')
    assert len(calls) == expected_calls


def prepared(tmp_path, monkeypatch):
    from scripts import studio_install_repair as repair
    from tests.test_studio_install import configuration
    config = configuration(tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "backend").mkdir()
    (tmp_path / "scripts/installer.py").write_text("CONNECT_TIMEOUT = 5\n")
    (tmp_path / "backend/app.py").write_text("APP_VERSION = 1\n")
    before = support.source_manifest(tmp_path)
    config.evidence.mkdir(parents=True)
    install.write_json(config.evidence / "source-manifest.json", before)
    install.write_json(config.evidence / "installation-inputs.json",
                       {"configuration": config.intent(), "source_sha256": digest(before)})
    package = config.evidence / "lambda.zip"
    package.write_bytes(b"the already uploaded application")
    native = {"request_token": "original-token", "status": "COMPLETE",
              "result": {"stack_id": "original-stack"}}
    state = {
        "target": config.binding,
        "installation": {"configuration": config.intent(), "source_sha256": digest(before),
                         "steps": {"build": {"status": "COMPLETE"},
                                   "application": {"status": "NEEDS_RECONCILIATION"}}},
        "installationBuild": {"files": {"lambda.zip": hashlib.sha256(package.read_bytes()).hexdigest()}},
        "platformOperations": {"install-stack-app": native},
    }
    install.write_json(config.state, state)
    calls = []

    def target(config, expected_source):
        calls.append(expected_source)
        value = PlatformTarget.__new__(PlatformTarget)
        value.path, value.binding = config.state, config.binding
        value.state = json.loads(config.state.read_text())
        assert value.state["installation"]["source_sha256"] == expected_source
        return value

    monkeypatch.setattr(repair, "InstallTarget", target)
    (tmp_path / "scripts/installer.py").write_text("CONNECT_TIMEOUT = 30\n")
    return repair, config, before, native, calls


def test_accepting_installer_fix_preserves_build_and_native_operations(tmp_path, monkeypatch):
    repair, config, before, native, calls = prepared(tmp_path, monkeypatch)
    result = repair.accept_update(config)
    assert result["changed_files"] == ["scripts/installer.py"]
    state = json.loads(config.state.read_text())
    assert state["platformOperations"] == {"install-stack-app": native}
    assert state["installation"]["steps"]["application"]["status"] == "NEEDS_RECONCILIATION"
    assert state["installation"]["source_sha256"] == support.source_digest(tmp_path)
    assert calls == [digest(before)]
    assert (config.evidence / "lambda.zip").read_bytes() == b"the already uploaded application"
    assert len(state["installation"]["installer_updates"]) == 1
    # Repeating a completed acceptance cannot add an update or repeat a native write.
    repair.accept_update(config)
    assert len(json.loads(config.state.read_text())["installation"]["installer_updates"]) == 1


@pytest.mark.parametrize("drift", ["application", "snapshot", "artifact", "config"])
def test_installer_acceptance_rejects_unrelated_or_unproven_changes(tmp_path, monkeypatch, drift):
    repair, config, before, _, calls = prepared(tmp_path, monkeypatch)
    if drift == "application":
        (tmp_path / "backend/app.py").write_text("APP_VERSION = 2\n")
    elif drift == "snapshot":
        install.write_json(config.evidence / "source-manifest.json", {})
    elif drift == "artifact":
        (config.evidence / "lambda.zip").write_bytes(b"replacement")
    else:
        inputs = json.loads((config.evidence / "installation-inputs.json").read_text())
        inputs["configuration"]["account"] = "999999999999"
        install.write_json(config.evidence / "installation-inputs.json", inputs)
    original = config.state.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        repair.accept_update(config)
    assert not calls
    assert config.state.read_bytes() == original


def test_acceptance_recovers_an_interrupted_local_receipt_update(tmp_path, monkeypatch):
    repair, config, before, native, calls = prepared(tmp_path, monkeypatch)
    write = repair.write_json
    interrupted = False

    def fail_once(path, value):
        nonlocal interrupted
        if path.name == "installation-inputs.json" and not interrupted:
            interrupted = True
            raise OSError("interrupted after durable state save")
        write(path, value)

    monkeypatch.setattr(repair, "write_json", fail_once)
    with pytest.raises(OSError):
        repair.accept_update(config)
    repair.accept_update(config)
    state = json.loads(config.state.read_text())
    assert len(state["installation"]["installer_updates"]) == 1
    assert state["platformOperations"] == {"install-stack-app": native}
    assert json.loads((config.evidence / "installation-inputs.json").read_text())["source_sha256"] == support.source_digest(tmp_path)


def test_older_receipt_accepts_only_a_snapshot_matching_its_recorded_digest(tmp_path, monkeypatch):
    repair, config, before, _, _ = prepared(tmp_path, monkeypatch)
    snapshot = tmp_path / "maintainer-provided-source-manifest.json"
    (config.evidence / "source-manifest.json").rename(snapshot)
    result = repair.accept_update(config, previous_manifest=snapshot)
    assert result["previous_source_sha256"] == digest(before)
    assert snapshot.exists()
