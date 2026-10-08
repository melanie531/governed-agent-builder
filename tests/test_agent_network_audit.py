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
def test_mcp_runtime_live_network_is_compared_to_its_pinned_receipt_at_its_bound_version(live, expected):
    control = Control(live)
    server = {"runtime_id": "python_abc-123", "runtime_version": "2", "network": VPC}
    checks = runtime_network_checks(control, server, VPC)
    assert checks["runtime_network_pinned"] is expected
    assert control.calls == [{"agentRuntimeId": "python_abc-123", "agentRuntimeVersion": "2"}]


def test_legacy_public_runtime_on_a_vpc_platform_passes_with_an_informational_mismatch():
    checks = runtime_network_checks(Control(PUBLIC), {"runtime_id": "python_legacy-1"}, VPC)
    assert checks["runtime_network_pinned"] is True
    assert "differs" in checks["runtime_network_platform"] and all(checks.values())


def test_runtime_matching_the_approved_network_reports_it():
    checks = runtime_network_checks(Control(REORDERED), {"runtime_id": "x", "network": VPC}, VPC)
    assert checks == {"runtime_network_pinned": True, "runtime_network_platform": "matches approved network"}


def test_servers_without_a_runtime_or_being_retired_are_not_read():
    control = Control(VPC)
    assert runtime_network_checks(control, {"phase": "PACKAGING"}, VPC) == {}
    assert runtime_network_checks(control, {"runtime_id": "x", "deletion": {"resources": []}}, VPC) == {}
    assert control.calls == []
