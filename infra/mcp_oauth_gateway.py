"""Separate Cognito Gateway using the repository's CloudFormation conventions.

This installs generic user OAuth capability only. Providers and MCP targets are
customer configuration and are never seeded by this template.
"""
import re

from backend.mcp_credentials import credential_prefix as validate_prefix
from infra.resource_tags import apply_resource_tags
from infra.serverless import attr, ref


def template(*, account, region, gateway_name, credential_prefix, table_name, issuer, client_id, worker_role_name):
    validate_prefix({"credential_prefix": credential_prefix})
    if (not re.fullmatch(r"\d{12}", account) or not re.fullmatch(r"[a-z]{2}-[a-z]+-\d", region)
            or not re.fullmatch(r"[a-z][a-z0-9-]{2,35}", gateway_name)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", table_name)
            or not re.fullmatch(r"[A-Za-z0-9+=,.@_-]{1,64}", worker_role_name)
            or not re.fullmatch(re.escape(f"https://cognito-idp.{region}.amazonaws.com/{region}_") + r"[A-Za-z0-9]+", issuer)
            or not re.fullmatch(r"[a-z0-9]{1,128}", client_id)):
        raise ValueError("Use this installation's exact account, region, table and Cognito client")
    root = f"arn:aws:bedrock-agentcore:{region}:{account}:"
    directory, vault = root + "workload-identity-directory/default", root + "token-vault/default"
    identity = directory + "/workload-identity/" + gateway_name + "-*"
    gateway_arn = root + "gateway/" + gateway_name + "-*"
    tags = {"auto-delete": "no", "project": "governed-agent-builder", "deployment": credential_prefix}
    def allow(actions, resources):
        return {"Effect": "Allow", "Action": actions, "Resource": resources}
    def policy(name, statements):
        return {"PolicyName": name, "PolicyDocument": {"Version": "2012-10-17", "Statement": statements}}
    resources = {
        "InterceptorLogs": {"Type": "AWS::Logs::LogGroup", "Properties": {
            "LogGroupName": "/governed-agent-builder/" + gateway_name + "/access", "RetentionInDays": 14}},
        "InterceptorRole": {"Type": "AWS::IAM::Role", "Properties": {
            "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]},
            "Policies": [policy("ReadCurrentGrants", [
                allow(["logs:CreateLogStream", "logs:PutLogEvents"], attr("InterceptorLogs")),
                {**allow(["dynamodb:GetItem", "dynamodb:Query"], f"arn:aws:dynamodb:{region}:{account}:table/{table_name}"),
                 "Condition": {"ForAllValues:StringEquals": {"dynamodb:LeadingKeys": [
                     "_revision", "principals", "components", "grants", "settings"]}}}])]}},
        "Interceptor": {"Type": "AWS::Lambda::Function", "Properties": {
            "FunctionName": gateway_name + "-access", "Runtime": "python3.13", "Architectures": ["arm64"],
            "Handler": "backend.mcp_gateway_interceptor.handler", "Role": attr("InterceptorRole"),
            "MemorySize": 512, "Timeout": 15,
            "Code": {"S3Bucket": ref("ArtifactBucket"), "S3Key": ref("ArtifactKey"),
                     "S3ObjectVersion": ref("ArtifactVersion")},
            "LoggingConfig": {"LogGroup": ref("InterceptorLogs")},
            "Environment": {"Variables": {"STATE_TABLE": table_name, "COGNITO_ISSUER": issuer,
                "COGNITO_CLIENT": client_id, "GATEWAY_NAME": gateway_name}}}},
        "GatewayRole": {"Type": "AWS::IAM::Role", "Properties": {
            "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"}, "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": account}, "ArnLike": {"aws:SourceArn": gateway_arn}}}]},
            "Policies": [policy("UserOAuthAndAccessCheck", [
                allow(["lambda:InvokeFunction"], attr("Interceptor")),
                allow(["bedrock-agentcore:GetWorkloadAccessToken",
                       "bedrock-agentcore:GetWorkloadAccessTokenForJWT"], [directory, identity]),
                allow(["bedrock-agentcore:GetResourceOauth2Token"], [
                    directory, identity, vault, vault + "/oauth2credentialprovider/" + credential_prefix + "-mcp-oauth-*"]),
                allow(["secretsmanager:GetSecretValue"],
                      f"arn:aws:secretsmanager:{region}:{account}:secret:{credential_prefix}/mcp/*")])]}},
        "Gateway": {"Type": "AWS::BedrockAgentCore::Gateway", "Properties": {
            "Name": gateway_name, "Description": "Studio Cognito inbound and per-user OAuth outbound",
            "ProtocolType": "MCP", "AuthorizerType": "CUSTOM_JWT", "RoleArn": attr("GatewayRole"),
            "ProtocolConfiguration": {"Mcp": {"SupportedVersions": ["2025-03-26", "2025-11-25"]}},
            "AuthorizerConfiguration": {"CustomJWTAuthorizer": {
                "DiscoveryUrl": issuer + "/.well-known/openid-configuration", "AllowedClients": [client_id],
                "AllowedScopes": ["openid"]}},
            "InterceptorConfigurations": [{"Interceptor": {"Lambda": {"Arn": attr("Interceptor")}},
                "InterceptionPoints": ["REQUEST"], "InputConfiguration": {"PassRequestHeaders": True}}],
            "Tags": tags}},
        "InterceptorPermission": {"Type": "AWS::Lambda::Permission", "Properties": {
            "Action": "lambda:InvokeFunction", "FunctionName": attr("Interceptor"),
            "Principal": "bedrock-agentcore.amazonaws.com", "SourceAccount": account, "SourceArn": attr("Gateway", "GatewayArn")}},
        # A managed policy avoids the existing worker's aggregate inline-policy
        # limit. CloudFormation owns the attachment. Its native IAM retention tag
        # is a required deployment step before this Gateway can be activated.
        "OnboardingPolicy": {"Type": "AWS::IAM::ManagedPolicy", "Properties": {
            "ManagedPolicyName": gateway_name + "-onboarding", "Roles": [worker_role_name],
            "PolicyDocument": {"Version": "2012-10-17", "Statement": [
                allow(["bedrock-agentcore:GetGateway", "bedrock-agentcore:ListGatewayTargets",
                       "bedrock-agentcore:GetGatewayTarget", "bedrock-agentcore:CreateGatewayTarget",
                       "bedrock-agentcore:DeleteGatewayTarget"],
                      [attr("Gateway", "GatewayArn"), {"Fn::Join": ["", [attr("Gateway", "GatewayArn"), "/target/*"]]}])]},
        }},
    }
    apply_resource_tags(resources)
    return {"AWSTemplateFormatVersion": "2010-09-09", "Description": "Generic user OAuth Gateway for the existing Cognito Studio",
        "Parameters": {key: {"Type": "String"} for key in ("ArtifactBucket", "ArtifactKey", "ArtifactVersion")},
        "Resources": resources, "Outputs": {
            "GatewayId": {"Value": attr("Gateway", "GatewayIdentifier")},
            "GatewayArn": {"Value": attr("Gateway", "GatewayArn")},
            "GatewayUrl": {"Value": attr("Gateway", "GatewayUrl")},
            "GatewayRoleArn": {"Value": attr("GatewayRole")},
            "OnboardingPolicyArn": {"Value": ref("OnboardingPolicy")},
            "InterceptorArn": {"Value": attr("Interceptor")}}}
