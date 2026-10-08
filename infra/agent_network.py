"""Customer-VPC network for AgentCore Runtimes. Deployed separately from the application stack."""
import json

from infra.serverless import ref, sub

TAGS = [{"Key": "project", "Value": "governed-agent-builder"}, {"Key": "auto-delete", "Value": "no"}]


def template():
    resources = {
        "RuntimeSecurityGroup": {"Type": "AWS::EC2::SecurityGroup", "Properties": {
            "GroupDescription": "AgentCore Runtime ENIs; egress only", "VpcId": ref("VpcId"),
            "SecurityGroupEgress": [{"IpProtocol": "-1", "CidrIp": "0.0.0.0/0", "Description": "Runtime egress"}],
            "Tags": TAGS}},
        "GatewayEndpointSecurityGroup": {"Type": "AWS::EC2::SecurityGroup", "Properties": {
            "GroupDescription": "AgentCore Gateway interface endpoint; TLS from Runtimes only", "VpcId": ref("VpcId"),
            "SecurityGroupIngress": [{"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443,
                "SourceSecurityGroupId": ref("RuntimeSecurityGroup"), "Description": "TLS from AgentCore Runtimes"}],
            "Tags": TAGS}},
        "GatewayEndpoint": {"Type": "AWS::EC2::VPCEndpoint", "Properties": {
            "ServiceName": sub("com.amazonaws.${AWS::Region}.bedrock-agentcore.gateway"),
            "VpcEndpointType": "Interface", "PrivateDnsEnabled": True,
            "VpcId": ref("VpcId"), "SubnetIds": ref("SubnetIds"),
            "SecurityGroupIds": [ref("RuntimeSecurityGroup"), ref("GatewayEndpointSecurityGroup")],
            "Tags": TAGS}},
    }
    return {"AWSTemplateFormatVersion": "2010-09-09",
            "Description": "Agent Studio Runtime security group and private AgentCore Gateway endpoint",
            "Parameters": {"VpcId": {"Type": "AWS::EC2::VPC::Id"}, "SubnetIds": {"Type": "List<AWS::EC2::Subnet::Id>"}},
            "Resources": resources,
            "Outputs": {"RuntimeSecurityGroupId": {"Value": ref("RuntimeSecurityGroup")},
                        "GatewayEndpointId": {"Value": ref("GatewayEndpoint")}}}


if __name__ == "__main__":
    print(json.dumps(template(), indent=2))
