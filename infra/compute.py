"""Isolated single-AZ fixture hosting. Render only; deployment is explicit."""
import json
from pathlib import Path

TAGS = [{"Key": k, "Value": v} for k, v in {"project": "governed-agent-builder", "managedBy": "cloudformation"}.items()]


def network_template():
    r = {
        "Vpc": {"Type": "AWS::EC2::VPC", "Properties": {"CidrBlock": "10.86.0.0/16", "EnableDnsSupport": True, "EnableDnsHostnames": True, "Tags": TAGS}},
        "InternetGateway": {"Type": "AWS::EC2::InternetGateway", "Properties": {"Tags": TAGS}},
        "GatewayAttachment": {"Type": "AWS::EC2::VPCGatewayAttachment", "Properties": {"VpcId": {"Ref": "Vpc"}, "InternetGatewayId": {"Ref": "InternetGateway"}}},
        "PublicSubnet": {"Type": "AWS::EC2::Subnet", "Properties": {"VpcId": {"Ref": "Vpc"}, "AvailabilityZone": {"Ref": "AvailabilityZone"}, "CidrBlock": "10.86.0.0/24", "MapPublicIpOnLaunch": False, "Tags": TAGS}},
        "PrivateSubnet": {"Type": "AWS::EC2::Subnet", "Properties": {"VpcId": {"Ref": "Vpc"}, "AvailabilityZone": {"Ref": "AvailabilityZone"}, "CidrBlock": "10.86.1.0/24", "MapPublicIpOnLaunch": False, "Tags": TAGS}},
        "PublicRoutes": {"Type": "AWS::EC2::RouteTable", "Properties": {"VpcId": {"Ref": "Vpc"}, "Tags": TAGS}},
        "PrivateRoutes": {"Type": "AWS::EC2::RouteTable", "Properties": {"VpcId": {"Ref": "Vpc"}, "Tags": TAGS}},
        "PublicAssociation": {"Type": "AWS::EC2::SubnetRouteTableAssociation", "Properties": {"SubnetId": {"Ref": "PublicSubnet"}, "RouteTableId": {"Ref": "PublicRoutes"}}},
        "PrivateAssociation": {"Type": "AWS::EC2::SubnetRouteTableAssociation", "Properties": {"SubnetId": {"Ref": "PrivateSubnet"}, "RouteTableId": {"Ref": "PrivateRoutes"}}},
        "InternetRoute": {"Type": "AWS::EC2::Route", "DependsOn": "GatewayAttachment", "Properties": {"RouteTableId": {"Ref": "PublicRoutes"}, "DestinationCidrBlock": "0.0.0.0/0", "GatewayId": {"Ref": "InternetGateway"}}},
        "NatAddress": {"Type": "AWS::EC2::EIP", "DependsOn": "GatewayAttachment", "Properties": {"Domain": "vpc", "Tags": TAGS}},
        "Nat": {"Type": "AWS::EC2::NatGateway", "Properties": {"AllocationId": {"Fn::GetAtt": ["NatAddress", "AllocationId"]}, "SubnetId": {"Ref": "PublicSubnet"}, "Tags": TAGS}},
        "NatRoute": {"Type": "AWS::EC2::Route", "Properties": {"RouteTableId": {"Ref": "PrivateRoutes"}, "DestinationCidrBlock": "0.0.0.0/0", "NatGatewayId": {"Ref": "Nat"}}},
        "Artifacts": {"Type": "AWS::S3::Bucket", "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain", "Properties": {
            "Tags": TAGS, "VersioningConfiguration": {"Status": "Enabled"}, "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]},
            "BucketEncryption": {"ServerSideEncryptionConfiguration": [{"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]},
            "PublicAccessBlockConfiguration": {k: True for k in ["BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets"]}}},
        "ArtifactPolicy": {"Type": "AWS::S3::BucketPolicy", "Properties": {"Bucket": {"Ref": "Artifacts"}, "PolicyDocument": {"Version": "2012-10-17", "Statement": [{"Effect": "Deny", "Principal": "*", "Action": "s3:*", "Resource": [{"Fn::GetAtt": ["Artifacts", "Arn"]}, {"Fn::Sub": "${Artifacts.Arn}/*"}], "Condition": {"Bool": {"aws:SecureTransport": "false"}}}]}}},
        "Logs": {"Type": "AWS::Logs::LogGroup", "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain", "Properties": {"LogGroupName": "/governed-agent-builder/preview", "RetentionInDays": 14, "Tags": TAGS}},
        "OriginSecurityGroup": {"Type": "AWS::EC2::SecurityGroup", "Properties": {"GroupDescription": "Private origin; ingress added only after CloudFront creates its service-managed SG", "VpcId": {"Ref": "Vpc"}, "SecurityGroupEgress": [{"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "CidrIp": "0.0.0.0/0", "Description": "TLS package bootstrap, Cognito JWKS/token, SSM and logs via NAT"}], "Tags": TAGS}},
        "Role": {"Type": "AWS::IAM::Role", "Properties": {"Tags": TAGS,
            "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]},
            "Policies": [{"PolicyName": "preview-only", "PolicyDocument": {"Version": "2012-10-17", "Statement": [
                {"Effect": "Allow", "Action": ["s3:GetObject", "s3:GetObjectVersion"], "Resource": {"Fn::Sub": "${Artifacts.Arn}/releases/*"}},
                {"Effect": "Allow", "Action": ["ssm:UpdateInstanceInformation", "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel", "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"], "Resource": "*"},
                {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"], "Resource": {"Fn::GetAtt": ["Logs", "Arn"]}},
            ]}}]}},
        "Profile": {"Type": "AWS::IAM::InstanceProfile", "Properties": {"Roles": [{"Ref": "Role"}]}},
        "DataVolume": {"Type": "AWS::EC2::Volume", "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain", "Properties": {"AvailabilityZone": {"Ref": "AvailabilityZone"}, "Encrypted": True, "VolumeType": "gp3", "Size": 20, "Tags": TAGS}},
    }
    return {"AWSTemplateFormatVersion": "2010-09-09", "Description": "Isolated Agent Studio fixture network, IAM and retained data", "Parameters": {"AvailabilityZone": {"Type": "AWS::EC2::AvailabilityZone::Name"}}, "Resources": r,
            "Outputs": {k: {"Value": {"Ref": v}} for k, v in {"VpcId": "Vpc", "SubnetId": "PrivateSubnet", "SecurityGroupId": "OriginSecurityGroup", "ArtifactBucket": "Artifacts", "InstanceProfile": "Profile", "DataVolumeId": "DataVolume", "RoleName": "Role"}.items()}}


def runtime_template():
    return {"AWSTemplateFormatVersion": "2010-09-09", "Description": "Private single-process EC2 fixture runtime; no public IP or SSH", "Parameters": {
        k: {"Type": "String"} for k in ["SubnetId", "SecurityGroupId", "InstanceProfile", "DataVolumeId", "ArtifactBucket", "ArtifactKey", "ArtifactSha256", "AmiId"]}, "Resources": {
        "Instance": {"Type": "AWS::EC2::Instance", "Properties": {
            "ImageId": {"Ref": "AmiId"}, "InstanceType": "t4g.small", "IamInstanceProfile": {"Ref": "InstanceProfile"},
            "NetworkInterfaces": [{"DeviceIndex": "0", "SubnetId": {"Ref": "SubnetId"}, "GroupSet": [{"Ref": "SecurityGroupId"}], "AssociatePublicIpAddress": False}],
            "MetadataOptions": {"HttpTokens": "required", "HttpEndpoint": "enabled", "HttpPutResponseHopLimit": 1},
            "BlockDeviceMappings": [{"DeviceName": "/dev/xvda", "Ebs": {"Encrypted": True, "VolumeSize": 12, "VolumeType": "gp3", "DeleteOnTermination": True}}],
            "Tags": TAGS + [{"Key": "Name", "Value": "governed-agent-builder-preview"}],
            "UserData": {"Fn::Base64": {"Fn::Sub": Path(__file__).with_name("bootstrap.sh").read_text()}},
        }},
        "DataAttachment": {"Type": "AWS::EC2::VolumeAttachment", "Properties": {"InstanceId": {"Ref": "Instance"}, "VolumeId": {"Ref": "DataVolumeId"}, "Device": "/dev/sdf"}},
    }, "Outputs": {"InstanceId": {"Value": {"Ref": "Instance"}}, "PrivateDnsName": {"Value": {"Fn::GetAtt": ["Instance", "PrivateDnsName"]}},
                      "PrivateOriginArn": {"Value": {"Fn::Sub": "arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:instance/${Instance}"}}}}


def ingress_template():
    return {"AWSTemplateFormatVersion": "2010-09-09", "Description": "Only CloudFront service-managed VPC origin SG may reach private HTTP", "Parameters": {k: {"Type": "AWS::EC2::SecurityGroup::Id"} for k in ["OriginSecurityGroup", "CloudFrontManagedSecurityGroup"]}, "Resources": {
        "CloudFrontOnly": {"Type": "AWS::EC2::SecurityGroupIngress", "Properties": {"GroupId": {"Ref": "OriginSecurityGroup"}, "SourceSecurityGroupId": {"Ref": "CloudFrontManagedSecurityGroup"}, "IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "Description": "Approved 2026-09-11 private preview HTTP; never public ingress"}}}}

if __name__ == "__main__":
    print(json.dumps(network_template(), indent=2))
