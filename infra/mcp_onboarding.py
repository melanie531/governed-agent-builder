"""Scoped permissions for generic MCP onboarding in an isolated deployment."""
import re

from backend.mcp_onboarding import configuration
from backend.mcp_credentials import credential_prefix
from infra.serverless import ref


def configure_app(resources, settings):
    config = configuration(settings["mcp_onboarding"], settings)
    account, region = settings["account"], settings["region"]
    gateway = f"arn:aws:bedrock-agentcore:{region}:{account}:gateway/{settings['gateway_id']}"
    if not re.fullmatch(r"[a-z0-9-]{1,100}", settings["gateway_id"]):
        raise ValueError("Invalid bound Gateway")
    registry = config["registry_arn"]
    secrets = config.get("secret_arns", [])
    secret_prefix = f"arn:aws:secretsmanager:{region}:{account}:secret:"
    if any(not re.fullmatch(re.escape(secret_prefix) + r"[A-Za-z0-9/_+=.@-]+", arn) for arn in secrets):
        raise ValueError("Credential secrets must belong to this account and region")
    identity = f"arn:aws:bedrock-agentcore:{region}:{account}:workload-identity-directory/default"
    vault = f"arn:aws:bedrock-agentcore:{region}:{account}:token-vault/default"
    statements = [
        {"Effect": "Allow", "Action": ["bedrock-agentcore:GetGateway", "bedrock-agentcore:ListGatewayTargets",
                                       "bedrock-agentcore:GetGatewayTarget", "bedrock-agentcore:CreateGatewayTarget",
                                       "bedrock-agentcore:DeleteGatewayTarget"],
         "Resource": [gateway, gateway + "/target/*"]},
        {"Effect": "Allow", "Action": ["bedrock-agentcore:InvokeGateway"], "Resource": gateway},
        {"Effect": "Allow", "Action": ["agent-registry:GetRegistry", "agent-registry:ListRegistryRecords",
                                       "agent-registry:GetRegistryRecord", "agent-registry:SubmitRegistryRecordForApproval",
                                       "agent-registry:UpdateRegistryRecordStatus", "agent-registry:DeleteRegistryRecord"],
         "Resource": [registry, registry + "/record/*"]},
        {"Effect": "Allow", "Action": ["agent-registry:CreateRegistryRecord"],
         "Resource": [registry, registry + "/record/*"],
         "Condition": {"StringEquals": {"aws:RequestTag/auto-delete": "no", "aws:RequestTag/project": "governed-agent-builder"}}},
        {"Effect": "Allow", "Action": ["agent-registry:TagResource"],
         "Resource": [registry + "/record/*"],
         "Condition": {"StringEquals": {"aws:RequestTag/auto-delete": "no", "aws:RequestTag/project": "governed-agent-builder"}}},
        {"Effect": "Allow", "Action": ["bedrock-agentcore:GetWorkloadAccessToken"],
         "Resource": [identity, identity + "/workload-identity/" + settings["gateway_id"]]},
    ]
    for kind, key, action in (("API_KEY", "apiKeyCredentialProvider", "bedrock-agentcore:GetResourceApiKey"),
                              ("OAUTH", "oauthCredentialProvider", "bedrock-agentcore:GetResourceOauth2Token")):
        providers = [c["configuration"]["credentialProvider"][key]["providerArn"] for c in config["connections"]
                     if c["configuration"]["credentialProviderType"] == kind]
        if providers:
            statements.append({"Effect": "Allow", "Action": [action],
                               "Resource": [*providers, vault, identity, identity + "/workload-identity/" + settings["gateway_id"]]})
    if secrets:
        statements.append({"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"], "Resource": secrets})
    if config.get("credential_prefix"):
        prefix = credential_prefix(config)
        provider = vault + "/apikeycredentialprovider/" + prefix + "-mcp-*"
        secret = secret_prefix + prefix + "/mcp/*"
        statements.extend(credential_use_statements(settings, prefix))
        required_tags = {"aws:RequestTag/auto-delete": "no", "aws:RequestTag/project": "governed-agent-builder",
                         "aws:RequestTag/deployment": prefix}
        resources["BusinessRole"]["Properties"]["Policies"].append({
            "PolicyName": "McpCredentialSetup", "PolicyDocument": {"Version": "2012-10-17", "Statement": [
                {"Effect": "Allow", "Action": ["secretsmanager:CreateSecret", "secretsmanager:TagResource"],
                 "Resource": secret, "Condition": {"StringEquals": required_tags}},
                # EXTERNAL provider creation validates the referenced value using
                # the caller's secret permission. Only this deployment's MCP
                # secrets are eligible; no API returns their values.
                {"Effect": "Allow", "Action": ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue",
                                              "secretsmanager:PutSecretValue", "secretsmanager:DeleteSecret"], "Resource": secret},
                # The service's get-or-create path authorizes this dependency
                # even when the account's default vault already exists.
                {"Effect": "Allow", "Action": ["bedrock-agentcore:CreateTokenVault", "bedrock-agentcore:GetTokenVault"], "Resource": vault},
                {"Effect": "Allow", "Action": ["bedrock-agentcore:CreateApiKeyCredentialProvider"],
                 "Resource": [vault, vault + "/apikeycredentialprovider/*"], "Condition": {"StringEquals": required_tags}},
                # Native CreateApiKeyCredentialProvider checks TagResource on
                # the wildcard provider ARN without request-tag context.
                # Creation itself requires tags; read-back verifies all tags
                # before admitting a connection. Credential use stays prefixed.
                {"Effect": "Allow", "Action": ["bedrock-agentcore:TagResource"],
                 "Resource": [vault, vault + "/apikeycredentialprovider/*"]},
                # AgentCore authorizes metadata lookup against both the provider
                # and its parent vault (including a not-yet-created provider).
                {"Effect": "Allow", "Action": ["bedrock-agentcore:GetApiKeyCredentialProvider",
                                              "bedrock-agentcore:DeleteApiKeyCredentialProvider"],
                 "Resource": [vault, provider]},
                {"Effect": "Allow", "Action": ["bedrock-agentcore:ListTagsForResource"],
                 "Resource": [vault, vault + "/apikeycredentialprovider/*"]},
            ]}})
    # Reuse exact unconditional grants already installed by MCP provisioning.
    # IAM counts all inline policies together; duplicate statements can exceed
    # the role's limit after CloudFormation resolves the resource ARNs.
    existing = [s for p in resources["WorkerRole"]["Properties"]["Policies"]
                for s in p["PolicyDocument"]["Statement"] if s["Effect"] == "Allow" and not s.get("Condition")]
    def values(value):
        return value if isinstance(value, list) else [value]
    def covered(statement):
        return not statement.get("Condition") and all(
            any(action in values(old.get("Action", [])) and resource in values(old.get("Resource", []))
                for old in existing)
            for action in values(statement["Action"]) for resource in values(statement["Resource"]))
    statements = [{**s, "Action": [a for a in values(s["Action"]) if not covered({**s, "Action": [a]})]} for s in statements]
    statements = [s for s in statements if s["Action"]]
    resources["WorkerRole"]["Properties"]["Policies"].append({
        "PolicyName": "McpOnboarding", "PolicyDocument": {"Version": "2012-10-17", "Statement": statements}})
    resources["BusinessRole"]["Properties"]["Policies"].append({
        "PolicyName": "McpRegistryRead", "PolicyDocument": {"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Action": ["agent-registry:GetRegistry", "agent-registry:GetRegistryRecord"],
            "Resource": [registry, registry + "/record/*"]}]}})
    resources.setdefault("RoleSwitchers", {"Type": "AWS::Cognito::UserPoolGroup", "Properties": {
        "UserPoolId": ref("Pool"), "GroupName": "studio-role-switcher",
        "Description": "Explicitly enrolled users can switch between assigned Studio roles"}})
    resources["Worker"]["Properties"]["Timeout"] = max(300, resources["Worker"]["Properties"]["Timeout"])
    resources["Jobs"]["Properties"]["VisibilityTimeout"] = max(
        resources["Jobs"]["Properties"]["VisibilityTimeout"], 6 * resources["Worker"]["Properties"]["Timeout"])


def credential_use_statements(settings, prefix):
    credential_prefix({"credential_prefix": prefix})
    region, account = settings["region"], settings["account"]
    root = f"arn:aws:bedrock-agentcore:{region}:{account}:"
    identity, vault = root + "workload-identity-directory/default", root + "token-vault/default"
    return [
        {"Effect": "Allow", "Action": ["bedrock-agentcore:GetResourceApiKey"], "Resource": [
            vault + "/apikeycredentialprovider/" + prefix + "-mcp-*", vault, identity,
            identity + "/workload-identity/" + settings["gateway_id"]]},
        {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"],
         "Resource": f"arn:aws:secretsmanager:{region}:{account}:secret:{prefix}/mcp/*"},
    ]


def configure_gateway(resources, settings):
    """Apply to the existing Journey stack; never mutate Gateway roles imperatively."""
    prefix = credential_prefix(settings["mcp_onboarding"])
    resources["GatewayRole"]["Properties"]["Policies"].append({
        "PolicyName": "McpCredentialUse", "PolicyDocument": {
            "Version": "2012-10-17", "Statement": credential_use_statements(settings, prefix)}})
