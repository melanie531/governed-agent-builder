import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws

from backend.dynamo_store import DynamoStore
from backend.foundation_runs import get, put
from scripts import configure_agent_network as configure
from tests.bootstrap_support import Target

ROOT = Path(__file__).resolve().parents[1]
CREDENTIALS = {"aws_access_key_id": "testing", "aws_secret_access_key": "testing", "region_name": "us-east-1"}


@pytest.fixture
def aws():
    with mock_aws():
        session = boto3.Session(**CREDENTIALS)
        ec2 = session.client("ec2")
        vpc = ec2.create_vpc(CidrBlock="10.20.0.0/16")["Vpc"]["VpcId"]
        subnets = [ec2.create_subnet(VpcId=vpc, CidrBlock=f"10.20.{i}.0/24", AvailabilityZone=zone)["Subnet"]["SubnetId"]
                   for i, zone in enumerate(("us-east-1a", "us-east-1b", "us-east-1a"))]
        group = ec2.create_security_group(GroupName="runtime", Description="runtime", VpcId=vpc)["GroupId"]
        resource = session.resource("dynamodb")
        resource.create_table(TableName="test-state", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"} for key in ("pk", "sk")])
        store = DynamoStore("test-state", resource)
        store.initialize()
        target = Target()
        target.session = session
        target.state["app"] = {"outputs": {"StateTable": "test-state"}}
        yield SimpleNamespace(target=target, ec2=ec2, vpc=vpc, subnets=subnets, group=group, store=store)


def expected(aws, subnets=None):
    return {"networkMode": "VPC", "networkModeConfig": {
        "subnets": subnets or aws.subnets[:2], "securityGroups": [aws.group]}}


def live(aws):
    with aws.store.tx() as db:
        return get(db, "journey-platform")


def seed_platform(aws, network=None):
    settings = {"enabled": True, "account": "123456789012", "region": "us-east-1",
                "network": network or {"networkMode": "PUBLIC"}}
    with aws.store.tx() as db:
        put(db, "journey-platform", settings)
    aws.target.state["journeyPlatform"] = settings
    return settings


def test_default_action_is_a_plan_that_writes_nothing(aws, capsys):
    seed_platform(aws)
    before = json.loads(json.dumps(aws.target.state))
    result = configure.run(aws.target, aws.vpc, aws.subnets[:2], [aws.group], apply=False)
    assert result["network"] == expected(aws)
    assert result["applied"] is False and result["validations"]
    assert aws.target.saves == [] and aws.target.state == before
    assert live(aws)["network"] == {"networkMode": "PUBLIC"}
    printed = json.loads(capsys.readouterr().out)
    assert printed["network"] == expected(aws) and printed["would_write"]


def test_apply_updates_bound_state_and_live_platform_record_consistently(aws):
    settings = seed_platform(aws)
    configure.run(aws.target, aws.vpc, aws.subnets[:2], [aws.group], apply=True)
    assert aws.target.state["agentNetwork"] == expected(aws)
    assert aws.target.state["journeyPlatform"] == {**settings, "network": expected(aws)}
    assert live(aws) == aws.target.state["journeyPlatform"]


def test_apply_before_installation_records_only_the_agent_network_choice(aws):
    configure.run(aws.target, aws.vpc, aws.subnets[:2], [aws.group], apply=True)
    assert aws.target.state["agentNetwork"] == expected(aws)
    assert "journeyPlatform" not in aws.target.state and live(aws) is None


def test_apply_refuses_when_live_record_and_bound_state_disagree(aws):
    seed_platform(aws)
    with aws.store.tx() as db:
        put(db, "journey-platform", {**aws.target.state["journeyPlatform"], "enabled": False})
    with pytest.raises(RuntimeError, match="differ"):
        configure.run(aws.target, aws.vpc, aws.subnets[:2], [aws.group], apply=True)
    assert aws.target.saves == []


def test_apply_resumes_after_live_record_was_already_updated(aws):
    settings = seed_platform(aws)
    with aws.store.tx() as db:
        put(db, "journey-platform", {**settings, "network": expected(aws)})
    configure.run(aws.target, aws.vpc, aws.subnets[:2], [aws.group], apply=True)
    assert aws.target.state["journeyPlatform"]["network"] == expected(aws) == live(aws)["network"]


def test_apply_lists_existing_runtimes_with_their_pinned_network_and_changes_none(aws):
    seed_platform(aws)
    vpc = expected(aws)
    servers = {"legacy": {"id": "legacy", "name": "Legacy", "phase": "READY", "runtime_id": "python_legacy-1"},
               "pinned": {"id": "pinned", "name": "Pinned", "phase": "READY", "network": vpc},
               "gone": {"id": "gone", "name": "Gone", "phase": "DELETED"}}
    with aws.store.tx() as db:
        for sid, server in servers.items():
            put(db, "mcp-python:" + sid, server)
    result = configure.run(aws.target, aws.vpc, aws.subnets[:2], [aws.group], apply=True)
    assert sorted(result["existing_runtimes"]["runtimes"], key=lambda r: r["id"]) == [
        {"id": "legacy", "name": "Legacy", "network": {"networkMode": "PUBLIC"}, "pinned": "legacy-PUBLIC"},
        {"id": "pinned", "name": "Pinned", "network": vpc, "pinned": "receipt"}]
    assert "retire and re-upload" in result["existing_runtimes"]["note"]
    with aws.store.tx() as db:
        assert {sid: get(db, "mcp-python:" + sid) for sid in servers} == servers


def test_apply_before_installation_skips_runtime_enumeration(aws):
    result = configure.run(aws.target, aws.vpc, aws.subnets[:2], [aws.group], apply=True)
    assert "existing_runtimes" not in result


def test_subnets_in_a_single_availability_zone_are_rejected(aws):
    with pytest.raises(RuntimeError, match="two Availability Zones"):
        configure.run(aws.target, aws.vpc, [aws.subnets[0], aws.subnets[2]], [aws.group], apply=True)
    assert aws.target.saves == []


def test_subnet_or_group_outside_the_vpc_is_rejected(aws):
    other = aws.ec2.create_vpc(CidrBlock="10.30.0.0/16")["Vpc"]["VpcId"]
    foreign_subnet = aws.ec2.create_subnet(VpcId=other, CidrBlock="10.30.0.0/24", AvailabilityZone="us-east-1b")["Subnet"]["SubnetId"]
    foreign_group = aws.ec2.create_security_group(GroupName="other", Description="other", VpcId=other)["GroupId"]
    with pytest.raises(RuntimeError, match="subnet.*not in"):
        configure.run(aws.target, aws.vpc, [aws.subnets[0], foreign_subnet], [aws.group], apply=True)
    with pytest.raises(RuntimeError, match="security group.*not in"):
        configure.run(aws.target, aws.vpc, aws.subnets[:2], [foreign_group], apply=True)
    assert aws.target.saves == []


def test_unknown_vpc_or_subnet_fails_with_an_actionable_error(aws):
    with pytest.raises(RuntimeError, match="VPC .* was not found"):
        configure.run(aws.target, "vpc-0123456789abcdef0", aws.subnets[:2], [aws.group], apply=True)
    with pytest.raises(RuntimeError, match="subnets .* not found"):
        configure.run(aws.target, aws.vpc, [aws.subnets[0], "subnet-0123456789abcdef0"], [aws.group], apply=True)


def test_subnet_with_a_direct_internet_gateway_default_route_is_rejected(aws):
    igw = aws.ec2.create_internet_gateway()["InternetGateway"]["InternetGatewayId"]
    aws.ec2.attach_internet_gateway(InternetGatewayId=igw, VpcId=aws.vpc)
    table = aws.ec2.create_route_table(VpcId=aws.vpc)["RouteTable"]["RouteTableId"]
    aws.ec2.create_route(RouteTableId=table, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw)
    aws.ec2.associate_route_table(RouteTableId=table, SubnetId=aws.subnets[1])
    with pytest.raises(RuntimeError, match="public subnet"):
        configure.run(aws.target, aws.vpc, aws.subnets[:2], [aws.group], apply=True)
    assert aws.target.saves == []


def test_public_main_route_table_applies_to_unassociated_subnets(aws):
    igw = aws.ec2.create_internet_gateway()["InternetGateway"]["InternetGatewayId"]
    aws.ec2.attach_internet_gateway(InternetGatewayId=igw, VpcId=aws.vpc)
    main = aws.ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [aws.vpc]},
        {"Name": "association.main", "Values": ["true"]}])["RouteTables"][0]["RouteTableId"]
    aws.ec2.create_route(RouteTableId=main, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw)
    with pytest.raises(RuntimeError, match="public subnet"):
        configure.run(aws.target, aws.vpc, aws.subnets[:2], [aws.group], apply=False)


@pytest.mark.parametrize("subnets,groups", [([], ["sg-1"]), (["subnet-a"], []), (["subnet-a", ""], ["sg-1"])])
def test_missing_inputs_never_fall_back_to_public(aws, subnets, groups):
    with pytest.raises((RuntimeError, ValueError)):
        configure.run(aws.target, aws.vpc, subnets, groups, apply=True)
    assert "agentNetwork" not in aws.target.state


def test_help_works_without_aws_credentials():
    env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
    env["AWS_CONFIG_FILE"] = env["AWS_SHARED_CREDENTIALS_FILE"] = os.devnull
    result = subprocess.run([sys.executable, "scripts/configure_agent_network.py", "--help"],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "--apply" in result.stdout and "--vpc-id" in result.stdout
