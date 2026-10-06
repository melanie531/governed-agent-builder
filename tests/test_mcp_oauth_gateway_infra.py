import json

from infra.mcp_oauth_gateway import template


def test_user_gateway_uses_cognito_and_scoped_interceptor_without_replacing_iam_gateway():
    value = template(account="123456789012", region="us-west-2", gateway_name="studio-user-tools",
        credential_prefix="customer-studio", table_name="customer-state",
        issuer="https://cognito-idp.us-west-2.amazonaws.com/us-west-2_Test", client_id="client123",
        worker_role_name="customer-worker")
    resources = value["Resources"]
    gateways = [r for r in resources.values() if r["Type"] == "AWS::BedrockAgentCore::Gateway"]
    assert len(gateways) == 1
    gateway = gateways[0]["Properties"]
    assert gateway["AuthorizerType"] == "CUSTOM_JWT"
    assert gateway["ProtocolConfiguration"]["Mcp"]["SupportedVersions"] == [
        "2025-03-26", "2025-11-25"]
    assert gateway["AuthorizerConfiguration"]["CustomJWTAuthorizer"]["AllowedClients"] == ["client123"]
    assert gateway["AuthorizerConfiguration"]["CustomJWTAuthorizer"]["AllowedScopes"] == ["openid"]
    assert gateway["InterceptorConfigurations"][0]["InterceptionPoints"] == ["REQUEST"]
    assert gateway["InterceptorConfigurations"][0]["InputConfiguration"]["PassRequestHeaders"]
    assert gateway["Tags"]["auto-delete"] == "no"
    assert not any(r["Type"] in ("AWS::BedrockAgentCore::GatewayTarget", "AWS::Lambda::Url") for r in resources.values())
    for resource in resources.values():
        if resource["Type"] == "AWS::IAM::Role":
            for policy in resource["Properties"]["Policies"]:
                for statement in policy["PolicyDocument"]["Statement"]:
                    assert statement["Resource"] != "*"
    text = json.dumps(value)
    assert "customer-studio/mcp/" in text
    assert "oauth2credentialprovider/customer-studio-mcp-oauth-*" in text
    role = resources["GatewayRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    exchange = [statement for statement in role
                if "bedrock-agentcore:GetWorkloadAccessToken" in statement["Action"]]
    assert len(exchange) == 1
    assert exchange[0]["Resource"] == [
        "arn:aws:bedrock-agentcore:us-west-2:123456789012:workload-identity-directory/default",
        "arn:aws:bedrock-agentcore:us-west-2:123456789012:workload-identity-directory/default/workload-identity/studio-user-tools-*",
    ]
    assert exchange[0]["Action"] == [
        "bedrock-agentcore:GetWorkloadAccessToken",
        "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
    ]
    assert "GetWorkloadAccessTokenForUserId" not in text
    assert "cognito-idp:Admin" not in text
