import hashlib
import json
from zipfile import ZipFile, ZipInfo

import pytest

from scripts.scoped_session_release import CHANGED, configuration_digest, verify_release


def fixture(tmp_path, monkeypatch):
    state = {"target": {"account": "123456789012", "region": "us-east-1", "profile": "default"},
             "app": {"stackId": "arn:aws:cloudformation:us-east-1:123456789012:stack/existing/id"},
             "artifacts": {"outputs": {"Bucket": "private-releases"}}}
    receipt = {"target": state["target"], "stack_id": state["app"]["stackId"],
               "previous_receipt": str(tmp_path / "previous.json"), "functions": {}, "operations": {}}
    for logical, changed in CHANGED.items():
        base, patch = tmp_path / (logical + "-base.zip"), tmp_path / (logical + ".zip")
        for path, value in ((base, "before"), (patch, "after")):
            with ZipFile(path, "w") as archive:
                for member in sorted(set(sum(CHANGED.values(), []))):
                    archive.writestr(ZipInfo(member), value if member in changed else "unchanged")
                archive.writestr(ZipInfo("backend/unrelated.py"), "preserved")
        digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        receipt["functions"][logical] = {
            "function": "existing-" + logical, "base_path": str(base), "package_path": str(patch),
            "base_sha256": digest(base), "patch_sha256": digest(patch),
            "changed_zip_members": list(changed),
            "upload": {"bucket": "private-releases", "key": "journey/session-renewal/" + digest(patch) + ".zip",
                       "digest": digest(patch), "version": "version-one"}}
        receipt["operations"][logical] = {"phase": "VERIFIED", "revision_before": "before", "revision_after": "after"}
    state["releaseSha256"] = receipt["functions"]["Auth"]["base_sha256"]
    monkeypatch.setattr("scripts.mcp_onboarding_audit.verified_scoped_session_auth_release",
                        lambda path, state, logical, name: receipt["functions"][logical]["base_sha256"])
    path = tmp_path / "release.json"
    path.write_text(json.dumps(receipt))
    return path, state, receipt


@pytest.mark.parametrize("logical", ["Auth", "Authorizer", "Business", "Worker"])
def test_release_accepts_only_exact_member_patch_and_verified_predecessor(tmp_path, monkeypatch, logical):
    path, state, receipt = fixture(tmp_path, monkeypatch)
    assert verify_release(path, state, logical, "existing-" + logical) == receipt["functions"][logical]["patch_sha256"]


@pytest.mark.parametrize("tamper", ["target", "stack", "function", "base", "unverified", "scope", "bucket", "key", "version"])
def test_release_fails_closed_for_unbound_or_incomplete_receipt(tmp_path, monkeypatch, tamper):
    path, state, receipt = fixture(tmp_path, monkeypatch)
    item = receipt["functions"]["Auth"]
    if tamper == "target": receipt["target"] = {**state["target"], "account": "999999999999"}
    if tamper == "stack": receipt["stack_id"] = "another-stack"
    if tamper == "function": item["function"] = "another-function"
    if tamper == "base": item["base_sha256"] = "0" * 64
    if tamper == "unverified": receipt["operations"]["Auth"]["phase"] = "INTENT"
    if tamper == "scope": item["changed_zip_members"].append("backend/unrelated.py")
    if tamper == "bucket": item["upload"]["bucket"] = "other-bucket"
    if tamper == "key": item["upload"]["key"] = "other-key"
    if tamper == "version": item["upload"].pop("version")
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        verify_release(path, state, "Auth", "existing-Auth")


def test_configuration_digest_excludes_code_update_fields_but_includes_security_settings():
    before = {"FunctionArn": "same", "Role": "scoped-role", "Timeout": 15,
              "Environment": {"Variables": {"HOSTED_PREVIEW": "1"}}, "RevisionId": "before", "CodeSha256": "old"}
    after = {**before, "RevisionId": "after", "CodeSha256": "new", "LastModified": "later"}
    assert configuration_digest(before) == configuration_digest(after)
    assert configuration_digest(before) != configuration_digest({**after, "Role": "different"})


def test_only_the_exact_observed_aws_runtime_patch_can_be_reconciled():
    from scripts.scoped_session_release import configuration_matches
    before = {"FunctionArn": "same", "Role": "scoped-role", "Runtime": "python3.13",
              "RuntimeVersionConfig": {"RuntimeVersionArn": "old-managed-runtime"}}
    after = {**before, "RuntimeVersionConfig": {"RuntimeVersionArn": "new-managed-runtime"}}
    item = {"configuration_sha256": configuration_digest(before), "managed_runtime_patch": {
        "before": before["RuntimeVersionConfig"], "after": after["RuntimeVersionConfig"],
        "update_runtime_on": "Auto"}}
    assert configuration_matches(before, item)
    assert configuration_matches(after, item)
    assert not configuration_matches({**after, "Role": "different"}, item)
    assert not configuration_matches({**after, "RuntimeVersionConfig": {
        "RuntimeVersionArn": "unreviewed-runtime"}}, item)
    assert not configuration_matches(after, {"configuration_sha256": item["configuration_sha256"]})
