import hashlib
import json
from zipfile import ZipFile

import pytest

from scripts.mcp_onboarding_audit import verified_scoped_deploy_only_release


def _fixture(tmp_path):
    files = {
        "base.zip": {"backend/journey.py": b"old deploy", "backend/journey_cloud.py": b"old recovery",
                     "backend/other.py": b"unchanged"},
        "worker-patched.zip": {"backend/journey.py": b"old deploy", "backend/journey_cloud.py": b"new recovery",
                               "backend/other.py": b"unchanged"},
        "business-deploy-only.zip": {"backend/journey.py": b"new deploy", "backend/journey_cloud.py": b"old recovery",
                                     "backend/other.py": b"unchanged"},
        "worker-deploy-only.zip": {"backend/journey.py": b"new deploy", "backend/journey_cloud.py": b"new recovery",
                                   "backend/other.py": b"unchanged"},
    }
    for name, members in files.items():
        with ZipFile(tmp_path / name, "w") as archive:
            for member, content in members.items():
                archive.writestr(member, content)
    sha = lambda name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
    state = {
        "target": {"account": "123456789012", "region": "us-east-1"},
        "app": {"stackId": "arn:aws:cloudformation:us-east-1:123456789012:stack/expected/abc"},
        "releaseSha256": sha("base.zip"),
    }
    prior = {"account": state["target"]["account"], "region": state["target"]["region"],
             "stack_id": state["app"]["stackId"], "function": "expected-Worker-ABC",
             "base_zip_sha256": sha("base.zip"), "patch_sha256": sha("worker-patched.zip"),
             "changed_zip_members": ["backend/journey_cloud.py"]}
    (tmp_path / "release-intent.json").write_text(json.dumps(prior))
    receipt = {"account": state["target"]["account"], "region": state["target"]["region"],
               "stack_id": state["app"]["stackId"], "base_zip_sha256": sha("base.zip"),
               "functions": {
                   "Business": {"function": "expected-Business-ABC", "base_sha256": sha("base.zip"),
                                "patch_sha256": sha("business-deploy-only.zip"),
                                "changed_zip_members": ["backend/journey.py"]},
                   "Worker": {"function": "expected-Worker-ABC", "base_sha256": sha("worker-patched.zip"),
                              "patch_sha256": sha("worker-deploy-only.zip"),
                              "changed_zip_members": ["backend/journey.py"]}}}
    path = tmp_path / "deploy-only-intent.json"
    path.write_text(json.dumps(receipt))
    return path, state, receipt, sha


@pytest.mark.parametrize("logical,function_name,file_name", [
    ("Business", "expected-Business-ABC", "business-deploy-only.zip"),
    ("Worker", "expected-Worker-ABC", "worker-deploy-only.zip"),
])
def test_scoped_deploy_only_release_accepts_exact_module_patch(tmp_path, logical, function_name, file_name):
    path, state, _, sha = _fixture(tmp_path)
    assert verified_scoped_deploy_only_release(path, state, logical, function_name) == sha(file_name)


@pytest.mark.parametrize("logical,function_name", [
    ("Business", "another-Business"),
    ("Worker", "another-Worker"),
])
def test_scoped_deploy_only_release_rejects_wrong_function(tmp_path, logical, function_name):
    path, state, _, _ = _fixture(tmp_path)
    with pytest.raises(ValueError, match="Scoped deploy-only"):
        verified_scoped_deploy_only_release(path, state, logical, function_name)


def test_scoped_deploy_only_release_rejects_extra_member_change(tmp_path):
    path, state, _, _ = _fixture(tmp_path)
    with ZipFile(tmp_path / "worker-deploy-only.zip", "w") as archive:
        archive.writestr("backend/journey.py", b"new deploy")
        archive.writestr("backend/journey_cloud.py", b"new recovery")
        archive.writestr("backend/other.py", b"changed too")
    with pytest.raises(ValueError, match="Scoped deploy-only"):
        verified_scoped_deploy_only_release(path, state, "Worker", "expected-Worker-ABC")
