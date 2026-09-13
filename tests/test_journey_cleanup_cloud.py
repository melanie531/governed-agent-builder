from types import SimpleNamespace

import pytest

from backend.journey_cleanup_cloud import purge_objects


def test_cleanup_deletes_all_object_versions_and_markers_only_under_agent_prefix():
    calls = []
    prefix = "journey/evidence/" + "a" * 64 + "/"
    s3 = SimpleNamespace(
        list_object_versions=lambda **kw: {"Versions": [{"Key": prefix + "case.json", "VersionId": "v1"},
                                                        {"Key": prefix + "case.json", "VersionId": "v2"}],
                                            "DeleteMarkers": [{"Key": prefix + "case.json", "VersionId": "marker"}]},
        delete_objects=lambda **kw: calls.append(kw) or {})
    cloud = SimpleNamespace(s3=s3, settings={"bucket": "project-private"})
    assert purge_objects(cloud, {"prefix": prefix, "exact": False}) is False
    assert {item["VersionId"] for item in calls[0]["Delete"]["Objects"]} == {"v1", "v2", "marker"}
    for unsafe in ("journey/", "journey/foundation/", "journey/evidence/", "other-project/"):
        with pytest.raises(ValueError, match="namespace"):
            purge_objects(cloud, {"prefix": unsafe, "exact": False})
    assert len(calls) == 1
