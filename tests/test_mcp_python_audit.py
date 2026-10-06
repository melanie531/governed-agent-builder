import base64
import hashlib

import pytest
from botocore.stub import Stubber

from tests.test_mcp_python import CONFIG
from tests.test_mcp_python_cloud import native


@pytest.mark.parametrize("phase", ["READY", "NEEDS_RECONCILIATION"])
def test_resource_audit_checks_discovery_failed_runtime_without_claiming_application_ready(monkeypatch, phase):
    from scripts.mcp_onboarding_audit import python_resource_checks
    cloud, state, _, runtime, _ = native()
    state.update(phase=phase, stage="schema", failure_code=None if phase == "READY" else "ValueError")
    calls = []
    def read(stage, *args):
        calls.append(stage)
        return {"package": {"artifact": state["artifact"]}, "runtime": {"runtime_id": runtime["agentRuntimeId"]},
                "logs": {"log_group": "owned"}}[stage]
    monkeypatch.setattr(cloud, "read", read)
    with Stubber(cloud.s3) as s3:
        s3.add_response("get_object_tagging", {"TagSet": [{"Key": "auto-delete", "Value": "no"}]}, {
            "Bucket": state["artifact"]["bucket"], "Key": state["artifact"]["key"],
            "VersionId": state["artifact"]["version_id"], "ExpectedBucketOwner": cloud.settings["account"]})
        checks = python_resource_checks(cloud, state, CONFIG)
        s3.assert_no_pending_responses()
    assert all(checks.values())
    assert calls == ["package", "runtime", "logs"]
    assert "ready" not in checks
    assert state["phase"] == phase


@pytest.mark.parametrize("fault", [None, "part_tag", "part_version", "unexpected_runtime", "wrong_failure"])
def test_rejected_package_audit_checks_frozen_parts_and_native_absence(monkeypatch, fault):
    from scripts.mcp_onboarding_audit import python_resource_checks
    cloud, state, _, _, _ = native()
    state.pop("artifact")
    digest = hashlib.sha256(b"rejected-zip").hexdigest()
    part = {"key": "mcp/python/uploads/" + state["id"] + "/part-0000",
            "version_id": "frozen-part-v1", "digest": digest, "size": 12}
    state.update(phase="FAILED", stage="package", failure_code="INVALID_PACKAGE",
                 upload_type="package", size=12, source_digest=digest, source_parts=[part])
    if fault == "wrong_failure":
        state["failure_code"] = "AUTHORITY_CHANGED"
    calls = []
    def read(stage, *args):
        calls.append(stage)
        return {"runtime_id": "unexpected"} if stage == "runtime" and fault == "unexpected_runtime" else None
    monkeypatch.setattr(cloud, "read", read)
    with Stubber(cloud.s3) as s3:
        s3.add_response("head_object", {
            "ContentLength": 12, "VersionId": "other" if fault == "part_version" else part["version_id"],
            "ServerSideEncryption": "AES256", "ChecksumSHA256": base64.b64encode(bytes.fromhex(digest)).decode(),
            "Metadata": {"upload-id": state["id"], "archive-digest": digest, "sha256": digest},
        }, {"Bucket": CONFIG["artifact"]["bucket"], "Key": part["key"], "VersionId": part["version_id"],
            "ExpectedBucketOwner": cloud.settings["account"], "ChecksumMode": "ENABLED"})
        s3.add_response("get_object_tagging", {
            "TagSet": [] if fault == "part_tag" else [{"Key": "auto-delete", "Value": "no"}],
        }, {"Bucket": CONFIG["artifact"]["bucket"], "Key": part["key"], "VersionId": part["version_id"],
            "ExpectedBucketOwner": cloud.settings["account"]})
        checks = python_resource_checks(cloud, state, CONFIG)
        s3.assert_no_pending_responses()
    assert all(checks.values()) is (fault is None)
    assert calls == ["package", "runtime"]
    assert state["phase"] == "FAILED" and "ready" not in checks
