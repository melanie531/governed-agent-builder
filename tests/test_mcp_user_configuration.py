from types import SimpleNamespace

import pytest

from backend.mcp_user_oauth import OAuthCloud, USER_TOKEN_HEADER

REGION, ACCOUNT, PREFIX = "us-west-2", "123456789012", "test-studio"
ROOT = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:"
RUNTIME = ROOT + "runtime/customer_mcp-AbCdEf1234"
AUTH = SimpleNamespace(issuer=f"https://cognito-idp.{REGION}.amazonaws.com/{REGION}_Test",
                       client_id="studioclient", public_url="https://studio.example.com")


def native():
    workload, provider = PREFIX + "-mcp-users-example", PREFIX + "-mcp-oauth-example"
    tags = {"auto-delete": "no", "deployment": PREFIX, "project": "governed-agent-builder"}
    runtime = {"agentRuntimeArn": RUNTIME, "status": "READY",
               "requestHeaderConfiguration": {"requestHeaderAllowlist": [USER_TOKEN_HEADER]},
               "environmentVariables": {
                   "STUDIO_TOKEN_ISSUER": AUTH.issuer, "STUDIO_TOKEN_CLIENT_ID": AUTH.client_id,
                   "OAUTH_WORKLOAD_NAME": workload, "OAUTH_PROVIDER_NAME": provider,
                   "OAUTH_SCOPES": '["refresh_token", "session:role:READER"]'}}
    identity = {"name": workload, "workloadIdentityArn": ROOT + "workload-identity-directory/default/workload-identity/" + workload,
                "allowedResourceOauth2ReturnUrls": [AUTH.public_url + "/oauth/callback"]}
    credential = {"name": provider, "credentialProviderArn": ROOT + "token-vault/default/oauth2credentialprovider/" + provider,
                  "status": "READY", "credentialProviderVendor": "CustomOauth2",
                  "clientSecretSource": "EXTERNAL", "clientSecretJsonKey": "client_secret",
                  "clientSecretArn": {"secretArn": f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:{PREFIX}/mcp/oauth/example-AbCd12"},
                  "oauth2ProviderConfigOutput": {"customOauth2ProviderConfig": {
                      "clientId": "providerclient", "oauthDiscovery": {"authorizationServerMetadata": {
                          "issuer": "https://provider.example.com", "authorizationEndpoint": "https://provider.example.com/authorize",
                          "tokenEndpoint": "https://provider.example.com/token", "responseTypes": ["code"]}}}}}
    control = SimpleNamespace(get_agent_runtime=lambda **_: runtime, get_workload_identity=lambda **_: identity,
                              get_oauth2_credential_provider=lambda **_: credential,
                              list_tags_for_resource=lambda **_: {"tags": tags})
    cloud = OAuthCloud({"account": ACCOUNT, "region": REGION}, control=control)
    return cloud, runtime, identity, credential, tags


def test_runtime_user_oauth_is_bound_to_this_installation_and_customer_workload():
    cloud, *_ = native()
    config = cloud.configuration(RUNTIME, PREFIX, AUTH)
    assert config["issuer"] == AUTH.issuer
    assert config["return_url"] == AUTH.public_url + "/oauth/callback"
    assert config["workload_name"] == PREFIX + "-mcp-users-example"
    assert config["provider_name"] == PREFIX + "-mcp-oauth-example"
    assert config["scopes"] == ["refresh_token", "session:role:READER"]
    assert "clientSecret" not in str(config)


@pytest.mark.parametrize("change", ["issuer", "client", "header", "workload", "callback", "tags", "provider", "secret"])
def test_user_oauth_metadata_drift_cannot_be_registered(change):
    cloud, runtime, identity, credential, tags = native()
    if change == "issuer":
        runtime["environmentVariables"]["STUDIO_TOKEN_ISSUER"] = "https://another.example.com"
    elif change == "client":
        runtime["environmentVariables"]["STUDIO_TOKEN_CLIENT_ID"] = "anotherclient"
    elif change == "header":
        runtime["requestHeaderConfiguration"] = {}
    elif change == "workload":
        runtime["environmentVariables"]["OAUTH_WORKLOAD_NAME"] = "another-workload"
    elif change == "callback":
        identity["allowedResourceOauth2ReturnUrls"] = ["https://another.example.com/callback"]
    elif change == "tags":
        tags["deployment"] = "another-studio"
    elif change == "secret":
        credential["clientSecretArn"]["secretArn"] = f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:another-studio/mcp/oauth/example-AbCd12"
    else:
        credential["status"] = "CREATE_FAILED"
    with pytest.raises(ValueError):
        cloud.configuration(RUNTIME, PREFIX, AUTH)


def test_gateway_service_linked_workload_is_not_obtained_by_studio():
    cloud, *_ = native()
    assert not hasattr(cloud, "gateway_workload")
