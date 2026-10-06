import hashlib
import json
from zipfile import ZipFile

import pytest

from scripts.scoped_consent_query_release import (
    CALLBACK_RESUME_CHANGED, CONNECTED_POPUP_CHANGED, NATIVE_GATEWAY_CHANGED, SESSION_AUTH_CHANGED,
    package_changes, reconcile_code)
from scripts.mcp_onboarding_audit import (
    verified_scoped_callback_resume_release,
    verified_scoped_session_auth_release,
    verified_scoped_connected_popup_release, verified_scoped_consent_query_release,
    verified_scoped_native_gateway_release,
)


def _zip(path, members):
    with ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_package_changes_accepts_only_three_reviewed_backend_members(tmp_path):
    base = tmp_path / "base.zip"
    patch = tmp_path / "patch.zip"
    before = {
        "backend/journey.py": b"old journey",
        "backend/journey_schema.py": b"old schema",
        "backend/journey_cloud.py": b"old cloud",
        "backend/other.py": b"unchanged",
    }
    after = {**before, "backend/journey.py": b"new journey",
             "backend/journey_schema.py": b"new schema",
             "backend/journey_cloud.py": b"new cloud"}
    base_sha, patch_sha = _zip(base, before), _zip(patch, after)
    assert package_changes(base, patch, base_sha, patch_sha) == [
        "backend/journey.py", "backend/journey_cloud.py", "backend/journey_schema.py"]


def test_package_changes_rejects_extra_member(tmp_path):
    base = tmp_path / "base.zip"
    patch = tmp_path / "patch.zip"
    before = {name: b"old" for name in (
        "backend/journey.py", "backend/journey_schema.py", "backend/journey_cloud.py", "other.py")}
    after = {name: b"new" for name in before}
    base_sha, patch_sha = _zip(base, before), _zip(patch, after)
    with pytest.raises(ValueError, match="changed members"):
        package_changes(base, patch, base_sha, patch_sha)


def test_connected_popup_package_accepts_only_two_reviewed_members(tmp_path):
    base = tmp_path / "base.zip"
    patch = tmp_path / "patch.zip"
    before = {"backend/journey.py": b"old journey",
              "backend/mcp_user_connections.py": b"old connections",
              "backend/other.py": b"unchanged"}
    after = {**before, "backend/journey.py": b"new journey",
             "backend/mcp_user_connections.py": b"new connections"}
    base_sha, patch_sha = _zip(base, before), _zip(patch, after)
    assert package_changes(base, patch, base_sha, patch_sha, CONNECTED_POPUP_CHANGED) == CONNECTED_POPUP_CHANGED
    after["backend/other.py"] = b"unexpected"
    patch_sha = _zip(patch, after)
    with pytest.raises(ValueError, match="changed members"):
        package_changes(base, patch, base_sha, patch_sha, CONNECTED_POPUP_CHANGED)


def test_package_changes_rejects_digest_drift(tmp_path):
    base = tmp_path / "base.zip"
    patch = tmp_path / "patch.zip"
    before = {name: b"old" for name in (
        "backend/journey.py", "backend/journey_schema.py", "backend/journey_cloud.py")}
    after = {name: b"new" for name in before}
    _zip(base, before)
    patch_sha = _zip(patch, after)
    with pytest.raises(ValueError, match="digest"):
        package_changes(base, patch, "0" * 64, patch_sha)


def test_native_gateway_release_verifies_only_four_reviewed_members_and_predecessor(tmp_path, monkeypatch):
    before = {name: b"old" for name in [*NATIVE_GATEWAY_CHANGED, "backend/other.py"]}
    after = {**before, **{name: b"new" for name in NATIVE_GATEWAY_CHANGED}}
    base, patch = tmp_path / "base.zip", tmp_path / "patch.zip"
    base_sha, patch_sha = _zip(base, before), _zip(patch, after)
    state = {"target": {"account": "123456789012", "profile": "default", "region": "us-east-1"},
             "app": {"stackId": "arn:aws:cloudformation:us-east-1:123456789012:stack/existing/id"}}
    receipt = {"target": state["target"], "stack_id": state["app"]["stackId"],
               "previous_receipt": str(tmp_path / "prior.json"),
               "base_paths": {name: str(base) for name in ("Business", "Worker")},
               "package_path": str(patch),
               "functions": {name: {"function": "existing-" + name, "base_sha256": base_sha,
                                    "patch_sha256": patch_sha, "changed_zip_members": NATIVE_GATEWAY_CHANGED}
                             for name in ("Business", "Worker")},
               "operations": {name: {"phase": "VERIFIED"} for name in ("Business", "Worker")},
               "upload": {"digest": patch_sha, "key": "journey/native-gateway/" + patch_sha + ".zip"}}
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))
    monkeypatch.setattr("scripts.mcp_onboarding_audit.verified_scoped_reauth_idle_release",
                        lambda *args: base_sha)
    assert verified_scoped_native_gateway_release(path, state, "Business", "existing-Business") == patch_sha
    after["backend/other.py"] = b"unreviewed"
    changed_sha = _zip(patch, after)
    for item in receipt["functions"].values():
        item["patch_sha256"] = changed_sha
    receipt["upload"] = {"digest": changed_sha, "key": "journey/native-gateway/" + changed_sha + ".zip"}
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="changed members"):
        verified_scoped_native_gateway_release(path, state, "Business", "existing-Business")


@pytest.mark.parametrize("kind,changed,verify,predecessor", [
    ("callback-resume", CALLBACK_RESUME_CHANGED, verified_scoped_callback_resume_release,
     "verified_scoped_native_gateway_release"),
    ("session-auth", SESSION_AUTH_CHANGED, verified_scoped_session_auth_release,
     "verified_scoped_callback_resume_release"),
])
def test_auth_release_verifies_only_reviewed_members_and_predecessor(tmp_path, monkeypatch, kind, changed, verify, predecessor):
    before = {name: b"old" for name in [*changed, "backend/other.py"]}
    after = {**before, **{name: b"new" for name in changed}}
    base, patch = tmp_path / "base.zip", tmp_path / "patch.zip"
    base_sha, patch_sha = _zip(base, before), _zip(patch, after)
    state = {"target": {"account": "123456789012", "profile": "default", "region": "us-east-1"},
             "app": {"stackId": "arn:aws:cloudformation:us-east-1:123456789012:stack/existing/id"}}
    receipt = {"target": state["target"], "stack_id": state["app"]["stackId"],
               "previous_receipt": str(tmp_path / "prior.json"),
               "base_paths": {"Business": str(base), "Worker": str(base)},
               "package_path": str(patch),
               "upload": {"digest": patch_sha, "key": "journey/" + kind + "/" + patch_sha + ".zip"},
               "functions": {logical: {
                   "function": "existing-" + logical, "base_sha256": base_sha,
                   "patch_sha256": patch_sha, "changed_zip_members": changed}
                   for logical in ("Business", "Worker")},
               "operations": {logical: {"phase": "VERIFIED"} for logical in ("Business", "Worker")}}
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))
    monkeypatch.setattr("scripts.mcp_onboarding_audit." + predecessor,
                        lambda *args: base_sha)
    assert verify(path, state, "Business", "existing-Business") == patch_sha
    with pytest.raises(ValueError, match="Scoped " + kind):
        verify(path, state, "Business", "another-Business")
    after["backend/other.py"] = b"unreviewed"
    changed_sha = _zip(patch, after)
    for item in receipt["functions"].values():
        item["patch_sha256"] = changed_sha
    receipt["upload"] = {"digest": changed_sha, "key": "journey/" + kind + "/" + changed_sha + ".zip"}
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="changed members"):
        verify(path, state, "Business", "existing-Business")


def test_reconcile_never_retries_an_intent_with_unchanged_live_code():
    intent = {"revision_before": "before", "phase": "INTENT"}
    live = {"RevisionId": "before", "CodeSha256": "old",
            "State": "Active", "LastUpdateStatus": "Successful"}
    assert reconcile_code(intent, live, "old", "new") == "uncertain"


def test_reconcile_identifies_success_after_lost_acknowledgement():
    intent = {"revision_before": "before", "phase": "INTENT"}
    live = {"RevisionId": "after", "CodeSha256": "new",
            "State": "Active", "LastUpdateStatus": "Successful"}
    assert reconcile_code(intent, live, "old", "new") == "verified"


def test_reconcile_does_not_accept_unrelated_code():
    intent = {"revision_before": "before", "phase": "ACKNOWLEDGED"}
    live = {"RevisionId": "after", "CodeSha256": "third",
            "State": "Active", "LastUpdateStatus": "Successful"}
    with pytest.raises(ValueError, match="outside"):
        reconcile_code(intent, live, "old", "new")


def _audit_fixture(tmp_path):
    previous = {name: b"old" for name in (
        "backend/journey.py", "backend/journey_cloud.py", "backend/journey_schema.py")}
    current = {name: b"new" for name in previous}
    bases = {}
    for logical in ("Business", "Worker"):
        base = tmp_path / (logical.lower() + "-base.zip")
        bases[logical] = {"path": str(base), "sha": _zip(base, previous)}
    package = tmp_path / "patch.zip"
    patch_sha = _zip(package, current)
    state = {"target": {"account": "123456789012", "profile": "default", "region": "us-east-1"},
             "app": {"stackId": "arn:aws:cloudformation:us-east-1:123456789012:stack/existing/id"}}
    receipt = {
        "target": state["target"], "stack_id": state["app"]["stackId"],
        "previous_receipt": str(tmp_path / "prior.json"),
        "base_paths": {logical: bases[logical]["path"] for logical in bases},
        "package_path": str(package),
        "functions": {
            logical: {"function": "existing-" + logical, "base_sha256": bases[logical]["sha"],
                      "patch_sha256": patch_sha,
                      "changed_zip_members": [
                          "backend/journey.py", "backend/journey_cloud.py", "backend/journey_schema.py"]}
            for logical in bases},
        "operations": {logical: {"phase": "VERIFIED"} for logical in bases}}
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))
    return path, state, receipt, patch_sha


def test_audit_binds_patched_lambda_to_predecessor(tmp_path, monkeypatch):
    path, state, receipt, sha = _audit_fixture(tmp_path)
    monkeypatch.setattr("scripts.mcp_onboarding_audit.verified_scoped_deploy_only_release",
                        lambda prior, bound_state, logical, function: receipt["functions"][logical]["base_sha256"])
    assert verified_scoped_consent_query_release(path, state, "Business", "existing-Business") == sha


def test_audit_rejects_wrong_function_or_missing_release_proof(tmp_path, monkeypatch):
    path, state, receipt, _ = _audit_fixture(tmp_path)
    monkeypatch.setattr("scripts.mcp_onboarding_audit.verified_scoped_deploy_only_release",
                        lambda prior, bound_state, logical, function: receipt["functions"][logical]["base_sha256"])
    with pytest.raises(ValueError, match="Scoped consent-query"):
        verified_scoped_consent_query_release(path, state, "Business", "another-Business")
    receipt["operations"]["Business"]["phase"] = "INTENT"
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="Scoped consent-query"):
        verified_scoped_consent_query_release(path, state, "Business", "existing-Business")


def test_connected_popup_audit_binds_two_member_patch_to_consent_query_predecessor(tmp_path, monkeypatch):
    before = {"backend/journey.py": b"old journey",
              "backend/mcp_user_connections.py": b"old connections",
              "backend/other.py": b"unchanged"}
    after = {**before, "backend/journey.py": b"new journey",
             "backend/mcp_user_connections.py": b"new connections"}
    base, patch = tmp_path / "base.zip", tmp_path / "patch.zip"
    base_sha, patch_sha = _zip(base, before), _zip(patch, after)
    state = {"target": {"account": "123456789012", "profile": "default", "region": "us-east-1"},
             "app": {"stackId": "arn:aws:cloudformation:us-east-1:123456789012:stack/existing/id"}}
    receipt = {"target": state["target"], "stack_id": state["app"]["stackId"],
               "previous_receipt": str(tmp_path / "prior.json"),
               "base_paths": {"Business": str(base), "Worker": str(base)},
               "package_path": str(patch),
               "upload": {"digest": patch_sha, "key": "journey/connected-popup/" + patch_sha + ".zip"},
               "functions": {logical: {
                   "function": "existing-" + logical, "base_sha256": base_sha,
                   "patch_sha256": patch_sha, "changed_zip_members": CONNECTED_POPUP_CHANGED}
                   for logical in ("Business", "Worker")},
               "operations": {logical: {"phase": "VERIFIED"} for logical in ("Business", "Worker")}}
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))
    monkeypatch.setattr("scripts.mcp_onboarding_audit.verified_scoped_consent_query_release",
                        lambda prior, bound_state, logical, function: base_sha)
    assert verified_scoped_connected_popup_release(path, state, "Business", "existing-Business") == patch_sha
    with pytest.raises(ValueError, match="Scoped connected-popup"):
        verified_scoped_connected_popup_release(path, state, "Business", "another-Business")


def test_reauth_release_binds_only_three_modules_to_connected_popup(tmp_path, monkeypatch):
    from scripts.scoped_consent_query_release import REAUTH_CHANGED
    from scripts.mcp_onboarding_audit import verified_scoped_reauth_release

    before = {name: b"old" for name in [*REAUTH_CHANGED, "backend/other.py"]}
    after = {**before, **{name: b"new" for name in REAUTH_CHANGED}}
    base, patch = tmp_path / "base.zip", tmp_path / "patch.zip"
    base_sha, patch_sha = _zip(base, before), _zip(patch, after)
    state = {"target": {"account": "123456789012", "profile": "default", "region": "us-east-1"},
             "app": {"stackId": "arn:aws:cloudformation:us-east-1:123456789012:stack/existing/id"}}
    receipt = {"target": state["target"], "stack_id": state["app"]["stackId"],
               "previous_receipt": str(tmp_path / "prior.json"),
               "base_paths": {"Business": str(base), "Worker": str(base)},
               "package_path": str(patch),
               "upload": {"digest": patch_sha, "key": "journey/reauth/" + patch_sha + ".zip"},
               "functions": {logical: {
                   "function": "existing-" + logical, "base_sha256": base_sha,
                   "patch_sha256": patch_sha, "changed_zip_members": REAUTH_CHANGED}
                   for logical in ("Business", "Worker")},
               "operations": {logical: {"phase": "VERIFIED"} for logical in ("Business", "Worker")}}
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))
    monkeypatch.setattr("scripts.mcp_onboarding_audit.verified_scoped_connected_popup_release",
                        lambda prior, bound_state, logical, function: base_sha)
    assert verified_scoped_reauth_release(path, state, "Business", "existing-Business") == patch_sha
    after["backend/other.py"] = b"wrong"
    patch_sha = _zip(patch, after)
    receipt["upload"] = {"digest": patch_sha, "key": "journey/reauth/" + patch_sha + ".zip"}
    for item in receipt["functions"].values():
        item["patch_sha256"] = patch_sha
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="changed members"):
        verified_scoped_reauth_release(path, state, "Business", "existing-Business")


def test_reauth_idle_release_binds_one_module_to_reauth_predecessor(tmp_path, monkeypatch):
    from scripts.scoped_consent_query_release import REAUTH_IDLE_CHANGED
    from scripts.mcp_onboarding_audit import verified_scoped_reauth_idle_release

    before = {"backend/mcp_user_connections.py": b"old", "backend/other.py": b"unchanged"}
    after = {**before, "backend/mcp_user_connections.py": b"new"}
    base, patch = tmp_path / "base.zip", tmp_path / "patch.zip"
    base_sha, patch_sha = _zip(base, before), _zip(patch, after)
    state = {"target": {"account": "123456789012", "profile": "default", "region": "us-east-1"},
             "app": {"stackId": "arn:aws:cloudformation:us-east-1:123456789012:stack/existing/id"}}
    receipt = {"target": state["target"], "stack_id": state["app"]["stackId"],
               "previous_receipt": str(tmp_path / "prior.json"),
               "base_paths": {"Business": str(base), "Worker": str(base)},
               "package_path": str(patch),
               "upload": {"digest": patch_sha, "key": "journey/reauth-idle/" + patch_sha + ".zip"},
               "functions": {logical: {
                   "function": "existing-" + logical, "base_sha256": base_sha,
                   "patch_sha256": patch_sha, "changed_zip_members": REAUTH_IDLE_CHANGED}
                   for logical in ("Business", "Worker")},
               "operations": {logical: {"phase": "VERIFIED"} for logical in ("Business", "Worker")}}
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))
    monkeypatch.setattr("scripts.mcp_onboarding_audit.verified_scoped_reauth_release",
                        lambda prior, bound_state, logical, function: base_sha)
    assert verified_scoped_reauth_idle_release(path, state, "Business", "existing-Business") == patch_sha
    with pytest.raises(ValueError, match="Scoped reauth-idle"):
        verified_scoped_reauth_idle_release(path, state, "Business", "wrong")
