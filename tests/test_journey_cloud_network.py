import pytest

from tests.test_journey_cloud import _runtime_recovery_fixture

VPC = {"networkMode": "VPC", "networkModeConfig": {"subnets": ["subnet-a", "subnet-b"], "securityGroups": ["sg-runtime"]}}


def test_business_agent_runtime_uses_the_stored_vpc_network():
    cloud, manifest, token, _, calls = _runtime_recovery_fixture(conflict=False)
    cloud.settings["network"] = VPC
    cloud.create(manifest, token)
    assert calls["create"][0]["networkConfiguration"] == VPC


@pytest.mark.parametrize("network", [{"networkMode": "VPC"}, {"networkMode": "PRIVATE"},
                                     {"networkMode": "VPC", "networkModeConfig": {"subnets": ["subnet-a"], "securityGroups": []}}])
def test_invalid_stored_network_fails_closed_before_any_write_or_deployment(network):
    cloud, manifest, token, _, calls = _runtime_recovery_fixture(conflict=False)
    cloud.settings["network"] = network
    cloud.write = lambda *_: pytest.fail("no manifest may be written for an invalid network")
    with pytest.raises(ValueError, match="network"):
        cloud.create(manifest, token)
    assert calls["create"] == []
