import pytest

from backend.network_policy import networks_equivalent, validate_network

VPC = {"networkMode": "VPC", "networkModeConfig": {"subnets": ["subnet-a", "subnet-b"], "securityGroups": ["sg-1"]}}


def test_public_and_vpc_networks_are_accepted_as_deep_copies():
    assert validate_network({"networkMode": "PUBLIC"}) == {"networkMode": "PUBLIC"}
    result = validate_network(VPC)
    assert result == VPC
    result["networkModeConfig"]["subnets"].append("subnet-c")
    assert VPC["networkModeConfig"]["subnets"] == ["subnet-a", "subnet-b"]


@pytest.mark.parametrize("network", [
    None, "PUBLIC", {}, {"networkMode": "public"}, {"networkMode": "PRIVATE"},
    {"networkMode": "PUBLIC", "networkModeConfig": VPC["networkModeConfig"]},
    {"networkMode": "VPC"},
    {"networkMode": "VPC", "networkModeConfig": {"subnets": ["subnet-a"]}},
    {"networkMode": "VPC", "networkModeConfig": {"subnets": [], "securityGroups": ["sg-1"]}},
    {"networkMode": "VPC", "networkModeConfig": {"subnets": ["subnet-a"], "securityGroups": []}},
    {"networkMode": "VPC", "networkModeConfig": {"subnets": "subnet-a", "securityGroups": ["sg-1"]}},
    {"networkMode": "VPC", "networkModeConfig": {"subnets": ["subnet-a", ""], "securityGroups": ["sg-1"]}},
    {"networkMode": "VPC", "networkModeConfig": {"subnets": ["subnet-a", 7], "securityGroups": ["sg-1"]}},
    {"networkMode": "VPC", "networkModeConfig": {"subnets": ["subnet-a"], "securityGroups": ["sg-1"], "extra": []}},
    {**VPC, "extra": True},
])
def test_invalid_networks_raise_instead_of_falling_back(network):
    with pytest.raises(ValueError):
        validate_network(network)


def test_networks_equivalent_ignores_list_order_only():
    reordered = {"networkMode": "VPC", "networkModeConfig": {"subnets": ["subnet-b", "subnet-a"], "securityGroups": ["sg-1"]}}
    assert networks_equivalent(VPC, reordered)
    assert networks_equivalent({"networkMode": "PUBLIC"}, {"networkMode": "PUBLIC"})
    assert not networks_equivalent(VPC, {"networkMode": "PUBLIC"})
    assert not networks_equivalent(VPC, {"networkMode": "VPC", "networkModeConfig": {"subnets": ["subnet-a"], "securityGroups": ["sg-1"]}})
    assert not networks_equivalent(VPC, {"networkMode": "VPC", "networkModeConfig": {"subnets": ["subnet-a", "subnet-b"], "securityGroups": ["sg-2"]}})
    assert not networks_equivalent(VPC, None)


def test_networks_equivalent_ignores_service_reported_vpc_fields():
    from backend.network_policy import networks_equivalent
    requested = {"networkMode": "VPC", "networkModeConfig": {
        "subnets": ["subnet-a", "subnet-b"], "securityGroups": ["sg-1"]}}
    reported = {"networkMode": "VPC", "networkModeConfig": {
        "securityGroups": ["sg-1"], "subnets": ["subnet-b", "subnet-a"],
        "requireServiceS3Endpoint": False}}
    assert networks_equivalent(reported, requested)
    different = {"networkMode": "VPC", "networkModeConfig": {
        "subnets": ["subnet-c"], "securityGroups": ["sg-1"], "requireServiceS3Endpoint": False}}
    assert not networks_equivalent(different, requested)
