import json

from oauth_infrastructure import oauth_template, runtime_oauth


CONFIG = {
    "studio_prefix": "customer-studio", "name": "snowflake",
    "issuer": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_AbC",
    "client_id": "studio123", "origin": "https://studio.example.com",
}


def test_oauth_stack_owns_tagged_identity_and_external_client_secret():
    template = oauth_template("customer-mcp", "123456789012", "us-east-1", "org-account", CONFIG)
    resources = template["Resources"]
    secret = next(v for v in resources.values() if v["Type"] == "AWS::SecretsManager::Secret")
    assert secret["Properties"]["Name"] == "customer-studio/mcp/oauth/snowflake"
    assert "GenerateSecretString" in secret["Properties"]
    identity = next(v for v in resources.values() if v["Type"] == "AWS::BedrockAgentCore::WorkloadIdentity")
    assert identity["Properties"]["Name"] == "customer-studio-mcp-users-snowflake"
    assert identity["Properties"]["AllowedResourceOauth2ReturnUrls"] == ["https://studio.example.com/oauth/callback"]
    provider = next(v for v in resources.values() if v["Type"] == "AWS::BedrockAgentCore::OAuth2CredentialProvider")
    config = provider["Properties"]["Oauth2ProviderConfigInput"]["CustomOauth2ProviderConfig"]
    assert config["ClientSecretSource"] == "EXTERNAL"
    assert config["ClientSecretConfig"]["JsonKey"] == "client_secret"
    assert config["ClientAuthenticationMethod"] == "CLIENT_SECRET_BASIC"
    assert config["ClientId"] == {"Ref": "OAuthClientId"}
    assert "ClientSecret" not in config
    for item in (secret, identity, provider):
        tags = {t["Key"]: t["Value"] for t in item["Properties"]["Tags"]}
        assert tags["auto-delete"] == "no"
        assert tags["deployment"] == "customer-studio"
    assert "CallbackUrl" in json.dumps(template["Outputs"])


def test_runtime_credentials_are_bound_to_one_customer_identity_and_provider():
    env, statements = runtime_oauth("123456789012", "us-east-1", CONFIG, "DATA_READER")
    assert json.loads(env["OAUTH_SCOPES"]) == ["session:role:DATA_READER", "refresh_token"]
    assert env["STUDIO_TOKEN_ISSUER"] == CONFIG["issuer"]
    assert env["STUDIO_TOKEN_CLIENT_ID"] == CONFIG["client_id"]
    allows = [s for s in statements if s["Effect"] == "Allow"]
    assert all(s["Resource"] != "*" for s in allows)
    assert any(s["Effect"] == "Deny" and "bedrock-agentcore:GetWorkloadAccessTokenForUserId" in s["Action"] for s in statements)
    assert not any("CompleteResourceTokenAuth" in json.dumps(s) for s in statements)
