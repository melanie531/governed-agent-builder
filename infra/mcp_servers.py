"""Scoped infrastructure for administrator-created Snowflake MCP servers."""
import re

from infra.serverless import ref


def credential_template():
    return {"AWSTemplateFormatVersion": "2010-09-09",
            "Description": "Retained Snowflake MCP provisioning credential; no credential value in IaC",
            "Resources": {"McpProvisionerPAT": {
                "Type": "AWS::SecretsManager::Secret", "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain",
                "Properties": {"Name": "governed-agent-builder-serverless/snowflake-mcp-provisioner",
                               "Description": "Snowflake MCP creator PAT; separate from the Gateway reader credential",
                               "Tags": [{"Key": "auto-delete", "Value": "no"},
                                        {"Key": "project", "Value": "governed-agent-builder"},
                                        {"Key": "journey", "Value": "create-mcp"}]}}},
            "Outputs": {"ProvisioningSecretArn": {"Value": ref("McpProvisionerPAT")}}}


def configure_app(resources, settings):
    account, region = settings["account"], settings["region"]
    creation = settings["mcp_creation"]
    secrets = [*creation["provisioning_secret_arns"], *creation["reader_secret_arns"]]
    prefix = f"arn:aws:secretsmanager:{region}:{account}:secret:governed-agent-builder-serverless/"
    if (not secrets or len(secrets) > 10
            or any(not arn.startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9/_+=.@-]+", arn[len(prefix):]) for arn in secrets)):
        raise ValueError("MCP provisioning secrets must be bound to this deployment")
    if not re.fullmatch(r"[a-z0-9-]{1,100}", settings["gateway_id"]):
        raise ValueError("Invalid bound Gateway")
    gateway = f"arn:aws:bedrock-agentcore:{region}:{account}:gateway/{settings['gateway_id']}"
    identity = f"arn:aws:bedrock-agentcore:{region}:{account}:workload-identity-directory/default"
    vault = f"arn:aws:bedrock-agentcore:{region}:{account}:token-vault/default"
    providers = creation["credential_provider_arns"]
    if not providers or any(not re.fullmatch(re.escape(vault) + r"/apikeycredentialprovider/[A-Za-z0-9_-]+", arn) for arn in providers):
        raise ValueError("MCP credential providers must be bound to this deployment")
    resources["WorkerRole"]["Properties"]["Policies"].append({
        "PolicyName": "McpCreation", "PolicyDocument": {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"], "Resource": secrets},
            {"Effect": "Allow", "Action": ["bedrock-agentcore:GetGateway", "bedrock-agentcore:ListGatewayTargets",
                                           "bedrock-agentcore:GetGatewayTarget", "bedrock-agentcore:CreateGatewayTarget"],
             "Resource": [gateway, gateway + "/target/*"]},
            {"Effect": "Allow", "Action": ["bedrock-agentcore:InvokeGateway"], "Resource": gateway},
            {"Effect": "Allow", "Action": ["bedrock-agentcore:GetWorkloadAccessToken"],
             "Resource": [identity, identity + "/workload-identity/" + settings["gateway_id"]]},
            {"Effect": "Allow", "Action": ["bedrock-agentcore:GetResourceApiKey"],
             "Resource": [*providers, vault, identity, identity + "/workload-identity/" + settings["gateway_id"]]},
        ]}})
    resources["RoleSwitchers"] = {"Type": "AWS::Cognito::UserPoolGroup", "Properties": {
        "UserPoolId": ref("Pool"), "GroupName": "studio-role-switcher",
        "Description": "Explicitly enrolled users may switch between their assigned business and admin roles"}}
    resources["Worker"]["Properties"]["Timeout"] = max(240, resources["Worker"]["Properties"]["Timeout"])
    resources["Jobs"]["Properties"]["VisibilityTimeout"] = max(
        resources["Jobs"]["Properties"]["VisibilityTimeout"], 6 * resources["Worker"]["Properties"]["Timeout"])
