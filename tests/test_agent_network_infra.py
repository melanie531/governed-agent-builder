import json
import re

from infra.agent_network import template


def test_parameters_bind_an_existing_vpc_and_its_subnets():
    value = template()
    assert value["Parameters"] == {"VpcId": {"Type": "AWS::EC2::VPC::Id"},
                                   "SubnetIds": {"Type": "List<AWS::EC2::Subnet::Id>"}}
    assert not any(r["Type"] in ("AWS::EC2::VPC", "AWS::EC2::Subnet") for r in value["Resources"].values())


def test_only_security_groups_and_the_gateway_endpoint_are_created():
    resources = template()["Resources"]
    assert sorted(r["Type"] for r in resources.values()) == [
        "AWS::EC2::SecurityGroup", "AWS::EC2::SecurityGroup", "AWS::EC2::VPCEndpoint"]
    body = json.dumps(template())
    assert "AWS::Lambda" not in body and "AWS::IAM" not in body and "AWS::DynamoDB" not in body


def test_runtime_security_group_is_egress_only_in_the_bound_vpc():
    group = template()["Resources"]["RuntimeSecurityGroup"]["Properties"]
    assert group["VpcId"] == {"Ref": "VpcId"}
    assert "SecurityGroupIngress" not in group
    assert group["SecurityGroupEgress"] == [{"IpProtocol": "-1", "CidrIp": "0.0.0.0/0", "Description": "Runtime egress"}]


def test_endpoint_security_group_admits_only_tls_from_the_runtime_group():
    group = template()["Resources"]["GatewayEndpointSecurityGroup"]["Properties"]
    assert group["VpcId"] == {"Ref": "VpcId"}
    assert group["SecurityGroupIngress"] == [{"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443,
        "SourceSecurityGroupId": {"Ref": "RuntimeSecurityGroup"}, "Description": "TLS from AgentCore Runtimes"}]


def test_gateway_interface_endpoint_uses_private_dns_in_the_bound_subnets():
    endpoint = template()["Resources"]["GatewayEndpoint"]["Properties"]
    assert endpoint["ServiceName"] == {"Fn::Sub": "com.amazonaws.${AWS::Region}.bedrock-agentcore.gateway"}
    assert endpoint["VpcEndpointType"] == "Interface"
    assert endpoint["PrivateDnsEnabled"] is True
    assert endpoint["VpcId"] == {"Ref": "VpcId"} and endpoint["SubnetIds"] == {"Ref": "SubnetIds"}
    assert endpoint["SecurityGroupIds"] == [{"Ref": "GatewayEndpointSecurityGroup"}]


def test_resources_are_tagged_and_outputs_expose_the_runtime_group():
    value = template()
    for resource in value["Resources"].values():
        tags = {t["Key"]: t["Value"] for t in resource["Properties"]["Tags"]}
        assert tags["project"] == "governed-agent-builder" and tags["auto-delete"] == "no"
    assert value["Outputs"]["RuntimeSecurityGroupId"] == {"Value": {"Ref": "RuntimeSecurityGroup"}}
    assert value["Outputs"]["GatewayEndpointId"] == {"Value": {"Ref": "GatewayEndpoint"}}


def test_template_has_no_account_or_region_specific_identifiers():
    body = json.dumps(template())
    assert "subnet-" not in body and "sg-" not in body and "vpc-" not in body
    assert not re.search(r"\d{12}", body)
    assert not re.search(r"(us|eu|ap|ca|sa|me|af)-[a-z]+-\d", body)
