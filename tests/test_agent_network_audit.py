import pytest

from scripts.agent_network_audit import approved_network, business_agent_checks, runtime_network_checks

VPC = {"networkMode": "VPC", "networkModeConfig": {"subnets": ["subnet-a", "subnet-b"], "securityGroups": ["sg-runtime"]}}
REORDERED = {"networkMode": "VPC", "networkModeConfig": {"subnets": ["subnet-b", "subnet-a"], "securityGroups": ["sg-runtime"]}}
PUBLIC = {"networkMode": "PUBLIC"}


class Control:
    def __init__(self, network):
        self.network, self.calls = network, []

    def get_agent_runtime(self, **request):
        self.calls.append(request)
        return {"networkConfiguration": self.network} if self.network else {}


def test_explicit_agent_network_is_the_approved_configuration():
    assert approved_network({"agentNetwork": VPC, "journeyPlatform": {"network": PUBLIC}}) == VPC


def test_legacy_states_approve_their_installed_network():
    assert approved_network({"journeyPlatform": {"network": PUBLIC}}) == PUBLIC
    assert approved_network({"journeyPlatform": {}}) == PUBLIC


def test_business_agent_network_must_match_the_approved_configuration():
    assert business_agent_checks({"agentNetwork": VPC, "journeyPlatform": {"network": REORDERED}}) == {"bound_network_approved": True}
    assert business_agent_checks({"agentNetwork": VPC, "journeyPlatform": {"network": PUBLIC}}) == {"bound_network_approved": False}


@pytest.mark.parametrize("live,expected", [(REORDERED, True), (PUBLIC, False), (None, False)])
def test_mcp_runtime_live_network_is_compared_at_its_bound_version(live, expected):
    control = Control(live)
    server = {"runtime_id": "python_abc-123", "runtime_version": "2"}
    assert runtime_network_checks(control, server, VPC) == {"runtime_network_approved": expected}
    assert control.calls == [{"agentRuntimeId": "python_abc-123", "agentRuntimeVersion": "2"}]


def test_servers_without_a_runtime_or_being_retired_are_not_read():
    control = Control(VPC)
    assert runtime_network_checks(control, {"phase": "PACKAGING"}, VPC) == {}
    assert runtime_network_checks(control, {"runtime_id": "x", "deletion": {"resources": []}}, VPC) == {}
    assert control.calls == []
