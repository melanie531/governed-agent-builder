from pathlib import Path

import pytest

from scripts.scoped_frontend_publish import RELEASE_VERIFIERS, bundle_manifest, previous_frontend_base
from scripts.mcp_onboarding_audit import verified_scoped_callback_resume_release, verified_scoped_connected_popup_release


def test_manifest_requires_one_built_script_and_existing_stylesheet(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(
        '<script type="module" src="/assets/index-new.js"></script>'
        '<link rel="stylesheet" href="/assets/index-old.css">')
    (tmp_path / "assets/index-new.js").write_bytes(b"app")
    (tmp_path / "assets/index-old.css").write_bytes(b"style")
    result = bundle_manifest(tmp_path)
    assert result["script"] == "assets/index-new.js"
    assert result["style"] == "assets/index-old.css"
    assert result["index_sha256"]


def test_manifest_rejects_missing_asset_and_localhost(tmp_path):
    (tmp_path / "index.html").write_text(
        '<script type="module" src="/assets/index-new.js"></script>'
        '<link rel="stylesheet" href="/assets/index-old.css">')
    with pytest.raises(ValueError, match="assets"):
        bundle_manifest(tmp_path)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/index-new.js").write_bytes(b"app")
    (tmp_path / "assets/index-old.css").write_bytes(b"style")
    (tmp_path / "index.html").write_text(
        '<script type="module" src="http://localhost:3000/app.js"></script>'
        '<link rel="stylesheet" href="/assets/index-old.css">')
    with pytest.raises(ValueError, match="asset references"):
        bundle_manifest(tmp_path)


def test_connected_popup_publication_requires_its_new_backend_receipt():
    assert RELEASE_VERIFIERS["connected-popup"] is verified_scoped_connected_popup_release


def test_callback_resume_publication_requires_its_new_backend_receipt():
    assert RELEASE_VERIFIERS["callback-resume"] is verified_scoped_callback_resume_release


def test_scoped_frontend_predecessor_must_be_verified_and_bound():
    prior = {
        "stack_id": "stack-one", "bucket": "bucket-one", "distribution_id": "distribution-one",
        "manifest": {"index_sha256": "a" * 64},
        "operations": {
            "index": {"phase": "VERIFIED", "version": "version-one", "sha256": "a" * 64},
            "script": {"phase": "VERIFIED"},
            "invalidation": {"phase": "VERIFIED"},
        },
    }
    assert previous_frontend_base(prior, "stack-one", "bucket-one", "distribution-one") == (
        "version-one", "a" * 64)
    prior["operations"]["invalidation"]["phase"] = "INTENT"
    with pytest.raises(ValueError, match="previous frontend"):
        previous_frontend_base(prior, "stack-one", "bucket-one", "distribution-one")
    prior["operations"]["invalidation"]["phase"] = "VERIFIED"
    with pytest.raises(ValueError, match="previous frontend"):
        previous_frontend_base(prior, "stack-one", "other-bucket", "distribution-one")


def test_original_frontend_predecessor_remains_supported():
    prior = {"stack_id": "stack-one", "frontend": {
        "bucket": "bucket-one", "distribution_id": "distribution-one",
        "index_version_after": "version-one", "index_sha256_after": "a" * 64}}
    assert previous_frontend_base(prior, "stack-one", "bucket-one", "distribution-one") == (
        "version-one", "a" * 64)


def test_onboarding_publication_requires_the_complete_current_canonical_release(tmp_path):
    import json

    sha = "a" * 64
    state = {"app": {"stackId": "stack-one"}, "releaseSha256": sha}
    receipt = {"stack_id": "stack-one", "release_sha256": sha, "deployment_verified": True,
               "functions": {"Business": {"name": "business-one", "sha256": sha}}}
    path = tmp_path / "release.json"
    path.write_text(json.dumps(receipt))
    verifier = RELEASE_VERIFIERS["onboarding"]
    assert verifier(path, state, "Business", "business-one") == sha
    for changed in (
        {**receipt, "deployment_verified": False},
        {**receipt, "stack_id": "another-stack"},
        {**receipt, "release_sha256": "b" * 64},
        {**receipt, "functions": {"Business": {"name": "business-other", "sha256": sha}}},
    ):
        path.write_text(json.dumps(changed))
        with pytest.raises(ValueError, match="onboarding release"):
            verifier(path, state, "Business", "business-one")
