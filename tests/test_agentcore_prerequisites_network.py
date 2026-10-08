from types import SimpleNamespace

import pytest

from infra.journey import RUNTIME_SERVICE_LINKED_ROLES
from scripts import agentcore_prerequisites as prerequisites
from tests.bootstrap_support import Target

NETWORK_ROLE = "AWSServiceRoleForBedrockAgentCoreNetwork"
VPC = {"networkMode": "VPC", "networkModeConfig": {"subnets": ["subnet-a", "subnet-b"], "securityGroups": ["sg-runtime"]}}


class Iam:
    class exceptions:
        class NoSuchEntityException(Exception):
            pass

    def __init__(self, account, network_role=None):
        self.roles = {name: f"arn:aws:iam::{account}:role/aws-service-role/{service}/{name}"
                      for name, service in RUNTIME_SERVICE_LINKED_ROLES.items()}
        if network_role:
            self.roles[NETWORK_ROLE] = network_role
        self.created, self.reads = [], []

    def get_role(self, RoleName):
        self.reads.append(RoleName)
        if RoleName not in self.roles:
            raise self.exceptions.NoSuchEntityException()
        return {"Role": {"RoleName": RoleName, "Arn": self.roles[RoleName]}}

    def create_service_linked_role(self, AWSServiceName):
        self.created.append(AWSServiceName)
        account = self.roles[next(iter(RUNTIME_SERVICE_LINKED_ROLES))].split(":")[4]
        self.roles[NETWORK_ROLE] = f"arn:aws:iam::{account}:role/aws-service-role/{AWSServiceName}/{NETWORK_ROLE}"
        return {"Role": {"RoleName": NETWORK_ROLE, "Arn": self.roles[NETWORK_ROLE]}}


def target_with(iam, network=None, key="journeyPlatform"):
    target = Target()
    target.session = SimpleNamespace(client=lambda name: iam)
    target.cf = None
    if network:
        target.state[key] = {"network": network} if key == "journeyPlatform" else network
    return target


def expected_arn(target):
    return (f"arn:aws:iam::{target.binding['account']}:role/aws-service-role/"
            f"network.bedrock-agentcore.amazonaws.com/{NETWORK_ROLE}")


def test_public_targets_do_not_read_or_create_the_network_role():
    iam = Iam(Target().binding["account"])
    prerequisites.install(target_with(iam, {"networkMode": "PUBLIC"}))
    assert NETWORK_ROLE not in iam.reads and iam.created == []


@pytest.mark.parametrize("key", ["journeyPlatform", "agentNetwork"])
def test_vpc_target_creates_and_reads_back_a_missing_network_role(key):
    iam = Iam(Target().binding["account"])
    target = target_with(iam, VPC, key)
    prerequisites.install(target)
    assert iam.created == ["network.bedrock-agentcore.amazonaws.com"]
    assert iam.reads.count(NETWORK_ROLE) == 2
    assert target.state["agentcoreNetworkPrerequisite"] == {"role": NETWORK_ROLE, "arn": expected_arn(target)}


def test_vpc_target_preserves_an_existing_network_role():
    account = Target().binding["account"]
    iam = Iam(account, f"arn:aws:iam::{account}:role/aws-service-role/network.bedrock-agentcore.amazonaws.com/{NETWORK_ROLE}")
    target = target_with(iam, VPC)
    prerequisites.install(target)
    assert iam.created == []
    assert target.state["agentcoreNetworkPrerequisite"]["arn"] == expected_arn(target)


def test_network_role_with_a_foreign_identity_is_refused():
    iam = Iam(Target().binding["account"], f"arn:aws:iam::999999999999:role/aws-service-role/network.bedrock-agentcore.amazonaws.com/{NETWORK_ROLE}")
    target = target_with(iam, VPC)
    with pytest.raises(ValueError, match="network service-linked role"):
        prerequisites.install(target)
    assert iam.created == [] and "agentcoreNetworkPrerequisite" not in target.state
