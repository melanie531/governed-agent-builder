from urllib.parse import quote

from scripts.mcp_onboarding_audit import reference_checks
from tests.test_mcp_user_configuration import native, AUTH, REGION, ACCOUNT, PREFIX, RUNTIME


def test_audit_retains_installation_prefix_for_legacy_user_iam_and_native_oauth():
    cloud, *_ = native()
    endpoint = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{quote(RUNTIME, safe='')}/invocations?qualifier=DEFAULT"
    settings = {"account": ACCOUNT, "region": REGION, "mcp_onboarding": {"credential_prefix": PREFIX}}
    request = {"phase": "READY", "auth_type": "GATEWAY_IAM_ROLE", "deployment_prefix": PREFIX, "endpoint": endpoint}
    connection = {"allowed_endpoints": [endpoint], "allowed_origins": [f"https://bedrock-agentcore.{REGION}.amazonaws.com"],
        "configuration": {"credentialProviderType": "GATEWAY_IAM_ROLE", "credentialProvider": {
            "iamCredentialProvider": {"service": "bedrock-agentcore", "region": REGION}}},
        "user_authorization": cloud.configuration(RUNTIME, PREFIX, AUTH)}
    assert all(reference_checks(request, connection, settings, AUTH, cloud).values())
    user = cloud.gateway_provider(PREFIX + "-mcp-oauth-example", PREFIX, AUTH, ["read"])
    settings["mcp_onboarding"]["oauth_gateway"] = {
        "gateway_id": "user-gateway-123",
        "gateway_url": f"https://user-gateway-123.gateway.bedrock-agentcore.{REGION}.amazonaws.com/mcp",
        "issuer": AUTH.issuer, "client_id": AUTH.client_id}
    connection.update(user_authorization=user, configuration={"credentialProviderType": "OAUTH", "credentialProvider": {
        "oauthCredentialProvider": {
            "providerArn": f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:token-vault/default/oauth2credentialprovider/" + user["provider_name"],
            "grantType": "AUTHORIZATION_CODE", "scopes": ["read"], "defaultReturnUrl": user["return_url"]}}})
    request["auth_type"] = "OAUTH"
    assert all(reference_checks(request, connection, settings, AUTH, cloud).values())
    request["phase"] = "DELETED"
    assert reference_checks(request, None, settings, AUTH, cloud) == {"metadata_absent": True}
