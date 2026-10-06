import hashlib
import zipfile

import pytest

from scripts.journey_foundation_patch import replace_member
from scripts.journey_foundation_release import activated_platform


def test_replace_member_changes_only_the_selected_runtime_source(tmp_path):
    base = tmp_path / "base.zip"
    with zipfile.ZipFile(base, "w") as archive:
        archive.writestr("foundation_harness/journey_mcp.py", "old transport")
        archive.writestr("runtime/journey/main.py", "preserve runtime")
    original = base.read_bytes()
    source = tmp_path / "journey_mcp.py"
    source.write_text("negotiated transport")
    output = tmp_path / "patched.zip"

    result = replace_member(
        base, hashlib.sha256(original).hexdigest(),
        "foundation_harness/journey_mcp.py", source, output,
    )

    assert base.read_bytes() == original
    assert result["changed_members"] == ["foundation_harness/journey_mcp.py"]
    assert result["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    with zipfile.ZipFile(output) as archive:
        assert archive.read("foundation_harness/journey_mcp.py") == b"negotiated transport"
        assert archive.read("runtime/journey/main.py") == b"preserve runtime"


def test_replace_member_rejects_unpinned_or_missing_source(tmp_path):
    base = tmp_path / "base.zip"
    with zipfile.ZipFile(base, "w") as archive:
        archive.writestr("runtime/journey/main.py", "preserve runtime")
    source = tmp_path / "journey_mcp.py"
    source.write_text("new transport")
    output = tmp_path / "patched.zip"

    with pytest.raises(ValueError, match="digest"):
        replace_member(base, "0" * 64, "runtime/journey/main.py", source, output)
    with pytest.raises(ValueError, match="missing"):
        replace_member(
            base, hashlib.sha256(base.read_bytes()).hexdigest(),
            "foundation_harness/journey_mcp.py", source, output,
        )
    assert not output.exists()


def test_gateway_force_capability_is_activated_only_with_new_foundation_artifact():
    before = {"artifact": {"sha256": "a" * 64}, "enabled": True}
    artifact = {"sha256": "b" * 64, "version_id": "reviewed"}
    after = activated_platform(before, artifact)
    assert after == {"artifact": artifact, "enabled": True, "gateway_force_auth_v1": True}
    assert before["artifact"]["sha256"] == "a" * 64
    assert "gateway_force_auth_v1" not in before
