import copy
import json

import pytest
from botocore.validate import validate_parameters

from backend.mcp_onboarding import configuration, binding_digest
from tests.test_mcp_onboarding_cloud import CONFIG, SETTINGS, STATE, adapter
from tests.test_mcp_onboarding import setup


GATEWAY = {
    "gateway_id": "studio-users-123", "gateway_url": "https://studio-users-123.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp",
    "issuer": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_Test", "client_id": "client123",
}
USER = {"mode": "gateway", "provider_name": "customer-studio-mcp-oauth-data",
        "provider_digest": "a" * 64, "authorization_origin": "https://data.example.com",
        "return_url": "https://studio.example.com/oauth/callback", "scopes": ["read"]}


def oauth_config():
    config = copy.deepcopy(CONFIG)
    config["credential_prefix"] = "customer-studio"
    config["oauth_gateway"] = copy.deepcopy(GATEWAY)
    connection = config["connections"][0]
    connection["configuration"] = {"credentialProviderType": "OAUTH", "credentialProvider": {
        "oauthCredentialProvider": {
            "providerArn": "arn:aws:bedrock-agentcore:us-west-2:123456789012:token-vault/default/oauth2credentialprovider/" + USER["provider_name"],
            "grantType": "AUTHORIZATION_CODE", "scopes": USER["scopes"], "defaultReturnUrl": USER["return_url"]}}}
    connection["user_authorization"] = USER
    return config


def test_native_oauth_target_uses_cognito_gateway_and_inline_schema_without_discovery():
    cloud, session, control, _ = adapter()
    calls = []
    config = oauth_config()
    arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:gateway/" + GATEWAY["gateway_id"]
    control.get_gateway = lambda **kw: {
        "gatewayArn": arn, "gatewayUrl": GATEWAY["gateway_url"], "status": "READY", "authorizerType": "CUSTOM_JWT",
        "authorizerConfiguration": {"customJWTAuthorizer": {
            "discoveryUrl": GATEWAY["issuer"] + "/.well-known/openid-configuration",
            "allowedClients": [GATEWAY["client_id"]], "allowedScopes": ["openid"]}}}
    def create(**kw):
        validate_parameters(kw, session._session.get_service_model("bedrock-agentcore-control").operation_model("CreateGatewayTarget").input_shape)
        calls.append(kw)
    control.create_gateway_target = create
    state = {**STATE, "tool_schema": STATE["tools"]}
    cloud.write("connect", state, config)
    assert calls[0]["gatewayIdentifier"] == GATEWAY["gateway_id"]
    assert calls[0]["credentialProviderConfigurations"] == [config["connections"][0]["configuration"]]
    assert json.loads(calls[0]["targetConfiguration"]["mcp"]["mcpServer"]["mcpToolSchema"]["inlinePayload"]) == {"tools": STATE["tools"]}
    assert "metadataConfiguration" not in calls[0]
    target = {"targetId": "target", "name": STATE["target_name"], "gatewayArn": arn, "status": "READY",
              "targetConfiguration": calls[0]["targetConfiguration"],
              "credentialProviderConfigurations": calls[0]["credentialProviderConfigurations"]}
    control.list_gateway_targets = lambda **_: {"items": [target]}
    control.get_gateway_target = lambda **_: target
    cloud.transport.discover = lambda: (_ for _ in ()).throw(AssertionError("Shared IAM discovery is forbidden"))
    tools = cloud.discover(state, config)
    assert tools[0]["name"] == STATE["target_name"] + "___list_datasets"
    target["targetConfiguration"] = copy.deepcopy(target["targetConfiguration"])
    target["targetConfiguration"]["mcp"]["mcpServer"]["mcpToolSchema"]["inlinePayload"] = '{"tools":[]}'
    with pytest.raises(ValueError, match="binding changed"):
        cloud.discover(state, config)


@pytest.mark.parametrize("scopes", [None, [], ["profile"], ["openid", "profile"]])
def test_native_oauth_onboarding_rejects_changed_access_token_scope(scopes):
    cloud, _, control, _ = adapter()
    authorizer = {
        "discoveryUrl": GATEWAY["issuer"] + "/.well-known/openid-configuration",
        "allowedClients": [GATEWAY["client_id"]],
    }
    if scopes is not None:
        authorizer["allowedScopes"] = scopes
    control.get_gateway = lambda **_: {
        "gatewayArn": "arn:aws:bedrock-agentcore:us-west-2:123456789012:gateway/" + GATEWAY["gateway_id"],
        "gatewayUrl": GATEWAY["gateway_url"], "status": "READY", "authorizerType": "CUSTOM_JWT",
        "authorizerConfiguration": {"customJWTAuthorizer": authorizer},
    }
    with pytest.raises(ValueError, match="Cognito Gateway binding"):
        cloud.validate({**STATE, "tool_schema": STATE["tools"]}, oauth_config())


@pytest.mark.parametrize("change", ["grant", "provider", "callback", "gateway", "scopes"])
def test_native_oauth_configuration_rejects_unbound_credentials(change):
    config = oauth_config()
    auth = config["connections"][0]["configuration"]["credentialProvider"]["oauthCredentialProvider"]
    if change == "grant":
        auth["grantType"] = "CLIENT_CREDENTIALS"
    elif change == "provider":
        auth["providerArn"] += "-other"
    elif change == "callback":
        auth["defaultReturnUrl"] = "https://elsewhere.example.com/oauth/callback"
    elif change == "gateway":
        config["oauth_gateway"]["gateway_url"] = "https://elsewhere.example.com/mcp"
    else:
        auth["scopes"] = ["admin"]
    with pytest.raises(ValueError):
        configuration(config, SETTINGS)


def test_adding_user_gateway_does_not_invalidate_existing_iam_or_api_key_registration():
    config = copy.deepcopy(CONFIG)
    state = {**STATE, "config_scope": "connection"}
    before = binding_digest(config, state)
    config["oauth_gateway"] = copy.deepcopy(GATEWAY)
    assert binding_digest(config, state) == before


def test_register_existing_oauth_provider_through_admin_api_and_delete_only_local_reference(setup):
    from backend.foundation_runs import get, put
    from tests.test_mcp_credentials import enable
    from tests.test_mcp_user_configuration import native, AUTH
    enable(setup)
    cloud, _, _, provider, _ = native()
    setup[2].oauth_cloud, setup[2].auth = cloud, AUTH
    with setup[1].tx() as db:
        config = get(db, "mcp-onboarding")
        config["oauth_gateway"] = {**GATEWAY, "issuer": AUTH.issuer, "client_id": AUTH.client_id}
        put(db, "mcp-onboarding", config)
    body = {"name": "User data access", "endpoint": STATE["endpoint"], "provider_arn": provider["credentialProviderArn"],
            "scopes": ["read"], "idempotency_key": "oauth-provider-reference-001"}
    response = setup[0].post("/api/admin/mcp/oauth-credentials", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["phase"] == "READY"
    assert setup[0].post("/api/admin/mcp/oauth-credentials", json=body).json() == response.json()
    assert setup[0].get("/api/admin/mcp/oauth-credentials/" + body["idempotency_key"]).json() == response.json()
    assert setup[0].post("/api/admin/mcp/oauth-credentials", json={**body, "scopes": ["admin"]}).status_code == 409
    path = "/api/admin/mcp/auth-connections/" + response.json()["connection_id"]
    detail = setup[0].get(path).json()
    assert detail["can_delete"] and not detail["can_edit"]
    assert setup[0].post(path + "/delete", json={
        "expected_revision": 1, "confirm_name": body["name"], "idempotency_key": "delete-oauth-reference-001",
    }).json()["phase"] == "DELETED"
    with setup[1].tx() as db:
        assert get(db, "mcp-auth:" + response.json()["connection_id"]) is None
