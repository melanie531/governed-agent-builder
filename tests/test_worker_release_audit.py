import hashlib
import json
from zipfile import ZipFile

import pytest

from scripts.mcp_onboarding_audit import verified_scoped_worker_release


def _fixture(tmp_path):
    base = tmp_path / "base.zip"
    patch = tmp_path / "worker-patched.zip"
    with ZipFile(base, "w") as archive:
        archive.writestr("backend/journey_cloud.py", b"old")
        archive.writestr("backend/other.py", b"unchanged")
    with ZipFile(patch, "w") as archive:
        archive.writestr("backend/journey_cloud.py", b"new")
        archive.writestr("backend/other.py", b"unchanged")
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    state = {
        "target": {"account": "123456789012", "region": "us-east-1"},
        "app": {"stackId": "arn:aws:cloudformation:us-east-1:123456789012:stack/expected/abc"},
        "releaseSha256": sha(base),
    }
    receipt = {
        "account": state["target"]["account"], "region": state["target"]["region"],
        "stack_id": state["app"]["stackId"],
        "function": "expected-Worker-ABC",
        "base_zip_sha256": sha(base), "patch_sha256": sha(patch),
        "changed_zip_members": ["backend/journey_cloud.py"],
    }
    path = tmp_path / "release-intent.json"
    path.write_text(json.dumps(receipt))
    return path, state, receipt, sha(patch)


def test_scoped_worker_audit_accepts_only_exact_patch_and_target(tmp_path):
    path, state, _, patch_sha = _fixture(tmp_path)
    assert verified_scoped_worker_release(path, state, "expected-Worker-ABC") == patch_sha


@pytest.mark.parametrize("field,value", [
    ("account", "999999999999"),
    ("function", "other-Worker"),
    ("base_zip_sha256", "0" * 64),
    ("changed_zip_members", ["backend/other.py"]),
])
def test_scoped_worker_audit_rejects_wrong_receipt(tmp_path, field, value):
    path, state, receipt, _ = _fixture(tmp_path)
    receipt[field] = value
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="Scoped Worker"):
        verified_scoped_worker_release(path, state, "expected-Worker-ABC")


def test_scoped_worker_audit_rejects_another_changed_member(tmp_path):
    path, state, _, _ = _fixture(tmp_path)
    with ZipFile(tmp_path / "worker-patched.zip", "w") as archive:
        archive.writestr("backend/journey_cloud.py", b"new")
        archive.writestr("backend/other.py", b"changed too")
    with pytest.raises(ValueError, match="Scoped Worker"):
        verified_scoped_worker_release(path, state, "expected-Worker-ABC")
