import json
from types import SimpleNamespace

import pytest

from backend.journey_cloud import JourneyCloud


@pytest.mark.parametrize("endpoint,expected", [
    ({"liveVersion": "1"}, True),  # Real READY responses omit targetVersion.
    ({"liveVersion": "1", "targetVersion": "1"}, True),
    ({"liveVersion": "2"}, False),
    ({"liveVersion": "1", "targetVersion": "2"}, False),
])
def test_ready_checks_live_version_and_optional_update_target(endpoint, expected):
    binding = {"id": "runtime-id", "arn": "runtime-arn", "version": "1", "manifest": {"digest": "manifest-digest"}}
    cloud = object.__new__(JourneyCloud)
    cloud.control = SimpleNamespace(
        get_agent_runtime=lambda **_: {"agentRuntimeArn": binding["arn"], "agentRuntimeVersion": "1", "status": "READY",
                                      "environmentVariables": {"JOURNEY_MANIFEST": json.dumps(binding["manifest"])}},
        get_agent_runtime_endpoint=lambda **_: {"status": "READY", "agentRuntimeArn": binding["arn"], **endpoint})
    assert cloud.ready(binding) is expected
