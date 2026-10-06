import copy
import json
from types import SimpleNamespace

import boto3
import pytest
from botocore.stub import Stubber

from backend.foundation_runs import get, put
from tests.test_mcp_credentials import BODY as API_BODY, CredentialCloud, enable
from tests.test_mcp_gateway_oauth import GATEWAY
from tests.test_mcp_onboarding import setup


BODY = {
    "auth_type": "OAUTH", "name": "Snowflake OAuth", "endpoint": "https://data.example.com/mcp",
    "client_id": "test-client", "secret": API_BODY["secret"],
    "issuer": "https://identity.example.com",
    "authorization_endpoint": "https://identity.example.com/authorize",
    "token_endpoint": "https://identity.example.com/token",
    "client_authentication_method": "CLIENT_SECRET_POST",
    "grant_type": "AUTHORIZATION_CODE", "scopes": ["read"],
    "idempotency_key": "managed-oauth-test-0001",
}


class OAuthCloud(CredentialCloud):
    def create_provider(self, state):
        self.writes.append("provider")
        self.providers[state["id"]] = (
            "arn:aws:bedrock-agentcore:us-west-2:123456789012:"
            "token-vault/default/oauth2credentialprovider/" + state["provider_name"])
        if self.lose == "provider":
            raise TimeoutError(BODY["secret"])

    def management_read(self, stage, state, operation):
        return state["id"] not in (self.providers if stage == "provider_delete" else self.secrets)

    def management_write(self, stage, state, operation, value=None):
        self.writes.append(stage)
        (self.providers if stage == "provider_delete" else self.secrets).pop(state["id"])


def managed(setup):
    credentials, _ = enable(setup)
    cloud = credentials.cloud = OAuthCloud()
    setup[2].auth = SimpleNamespace(
        issuer=GATEWAY["issuer"], client_id=GATEWAY["client_id"], public_url="https://studio.example.com")
    with setup[1].tx() as db:
        config = get(db, "mcp-onboarding")
        config["oauth_gateway"] = copy.deepcopy(GATEWAY)
        put(db, "mcp-onboarding", config)
    return cloud


def managed_owner_and_reference(setup, grant="AUTHORIZATION_CODE"):
    from backend.mcp_credentials import oauth_provider_config
    from tests.test_mcp_user_configuration import native
    cloud = managed(setup)
    response = setup[0].post("/api/admin/mcp/credentials", json={**BODY, "grant_type": grant})
    assert response.status_code == 200 and response.json()["phase"] == "READY", response.text
    owner = response.json()
    reader, _, _, provider, _ = native()
    provider.update(name=cloud.providers[owner["id"]].rsplit("/", 1)[-1],
        credentialProviderArn=cloud.providers[owner["id"]],
        oauth2ProviderConfigOutput=oauth_provider_config(BODY))
    setup[2].oauth_cloud = reader
    reference = {"name": "Shared OAuth reference", "endpoint": BODY["endpoint"] + "/shared",
        "provider_arn": cloud.providers[owner["id"]], "scopes": BODY["scopes"],
        "idempotency_key": "shared-oauth-reference-0001"}
    return cloud, owner, reference


def owner_deletion():
    return {"expected_revision": 1, "confirm_name": BODY["name"],
        "idempotency_key": "delete-managed-oauth-0001"}


@pytest.mark.parametrize("grant", ["AUTHORIZATION_CODE", "CLIENT_CREDENTIALS"])
def test_shared_oauth_provider_blocks_owner_deletion_until_imported_reference_is_deleted(setup, grant):
    cloud, owner, body = managed_owner_and_reference(setup, grant)
    response = setup[0].post("/api/admin/mcp/oauth-credentials", json=body)
    assert response.status_code == 200, response.text
    alias = response.json()
    path = "/api/admin/mcp/auth-connections/" + owner["connection_id"]
    info = setup[0].get(path).json()
    blocked = setup[0].post(path + "/delete", json=owner_deletion())
    assert blocked.status_code == 409, blocked.text
    assert not info["can_delete"]
    assert any(r["id"] == alias["connection_id"] for r in info["references"])
    assert cloud.writes == ["secret", "provider"]

    alias_path = "/api/admin/mcp/auth-connections/" + alias["connection_id"]
    assert setup[0].get(alias_path).json()["can_delete"]
    deleted = setup[0].post(alias_path + "/delete", json={
        "expected_revision": 1, "confirm_name": body["name"], "idempotency_key": "delete-shared-reference-0001"})
    assert deleted.status_code == 200 and deleted.json()["phase"] == "DELETED", deleted.text
    assert setup[0].post("/api/admin/mcp/oauth-credentials", json=body).json()["phase"] == "DELETED"
    assert cloud.writes == ["secret", "provider"]
    assert owner["id"] in cloud.providers and owner["id"] in cloud.secrets
    assert setup[0].get(path).json()["can_delete"]
    deleted = setup[0].post(path + "/delete", json=owner_deletion())
    assert deleted.status_code == 200 and deleted.json()["phase"] == "DELETED", deleted.text
    assert cloud.writes == ["secret", "provider", "provider_delete", "secret_delete"]


def test_deployment_configured_oauth_reference_also_blocks_managed_owner_deletion(setup):
    cloud, owner, body = managed_owner_and_reference(setup)
    response = setup[0].post("/api/admin/mcp/oauth-credentials", json=body)
    assert response.status_code == 200, response.text
    cid = response.json()["connection_id"]
    with setup[1].tx() as db:
        config = get(db, "mcp-onboarding")
        config["connections"].append(get(db, "mcp-auth:" + cid))
        put(db, "mcp-onboarding", config)
        db.delete("settings", where=[("key", "=", "mcp-auth:" + cid)])
    path = "/api/admin/mcp/auth-connections/" + owner["connection_id"]
    response = setup[0].post(path + "/delete", json=owner_deletion())
    assert response.status_code == 409, response.text
    assert not setup[0].get(path).json()["can_delete"]
    assert cloud.writes == ["secret", "provider"]


@pytest.mark.parametrize("timing", ["retained_before_read", "retained_after_read", "deleted_after_read"])
def test_oauth_import_rechecks_owner_deletion_after_native_provider_read(setup, monkeypatch, timing):
    from backend.mcp_auth_management import AuthManagement
    cloud, owner, body = managed_owner_and_reference(setup)
    if timing.startswith("retained"):
        def uncertain_delete(stage, state, operation, value=None):
            cloud.writes.append(stage)
            raise TimeoutError("Synthetic uncertain deletion; provider still exists")
        monkeypatch.setattr(cloud, "management_write", uncertain_delete)

    def delete_owner():
        result = AuthManagement(setup[2].credentials).change(
            {"id": "admin", "role": "admin"}, owner["connection_id"], owner_deletion(), "delete")
        assert result["phase"] == ("NEEDS_RECONCILIATION" if timing.startswith("retained") else "DELETED")

    if timing == "retained_before_read":
        delete_owner()
    else:
        read_provider = setup[2].oauth_cloud.gateway_provider
        def read_then_delete(*args):
            result = read_provider(*args)
            # The native GET succeeded, but deletion wins before the import commits.
            delete_owner()
            return result
        monkeypatch.setattr(setup[2].oauth_cloud, "gateway_provider", read_then_delete)
    response = setup[0].post("/api/admin/mcp/oauth-credentials", json=body)
    assert response.status_code == 409, response.text
    assert setup[0].get("/api/admin/mcp/oauth-credentials/" + body["idempotency_key"]).status_code == 404
    with setup[1].tx() as db:
        assert not any(c["name"] == body["name"] for c in setup[2].config(db)["connections"])
    assert cloud.writes.count("provider_delete") == 1


def test_retained_owner_deletion_rechecks_shared_references_before_retry(setup, monkeypatch):
    cloud, owner, body = managed_owner_and_reference(setup)
    response = setup[0].post("/api/admin/mcp/oauth-credentials", json=body)
    assert response.status_code == 200, response.text
    cid = response.json()["connection_id"]
    with setup[1].tx() as db:
        alias = get(db, "mcp-auth:" + cid)
        db.delete("settings", where=[("key", "=", "mcp-auth:" + cid)])
    write = cloud.management_write
    def uncertain_delete(stage, state, operation, value=None):
        cloud.writes.append(stage)
        raise TimeoutError("Synthetic uncertain deletion")
    monkeypatch.setattr(cloud, "management_write", uncertain_delete)
    path = "/api/admin/mcp/auth-connections/" + owner["connection_id"]
    response = setup[0].post(path + "/delete", json=owner_deletion())
    assert response.json()["phase"] == "NEEDS_RECONCILIATION"
    with setup[1].tx() as db:
        # Retained operations may already have aliases admitted before this fix.
        put(db, "mcp-auth:" + cid, alias)
    monkeypatch.setattr(cloud, "management_write", write)
    continuation = path + "/operations/" + owner_deletion()["idempotency_key"] + "/continue"
    writes = list(cloud.writes)
    response = setup[0].post(continuation, json={"retry": True})
    assert response.status_code == 409, response.text
    assert cloud.writes == writes
    assert owner["id"] in cloud.providers and owner["id"] in cloud.secrets
    deleted = setup[0].post("/api/admin/mcp/auth-connections/" + cid + "/delete", json={
        "expected_revision": 1, "confirm_name": body["name"], "idempotency_key": "delete-shared-reference-0001"})
    assert deleted.status_code == 200 and deleted.json()["phase"] == "DELETED", deleted.text
    assert cloud.writes == writes
    response = setup[0].post(continuation, json={"retry": True})
    assert response.status_code == 200 and response.json()["phase"] == "DELETED", response.text
    assert cloud.writes == writes + ["provider_delete", "secret_delete"]


@pytest.mark.parametrize("grant", ["AUTHORIZATION_CODE", "CLIENT_CREDENTIALS"])
def test_create_managed_oauth_uses_correct_gateway_grant_and_never_persists_client_secret(setup, grant):
    cloud = managed(setup)
    body = {**BODY, "grant_type": grant}
    response = setup[0].post("/api/admin/mcp/credentials", json=body)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["phase"] == "READY"
    assert setup[0].post("/api/admin/mcp/credentials", json=body).json() == value
    assert cloud.writes == ["secret", "provider"]
    with setup[1].tx() as db:
        connection = get(db, "mcp-auth:" + value["connection_id"])
        persisted = json.dumps([dict(r) for r in db.select("settings")] + [dict(r) for r in db.select("audit")])
        # Exercise the real onboarding validator, including 3LO Gateway binding.
        assert setup[2].config(db)["connections"][-1] == connection
    assert BODY["secret"] not in persisted + response.text
    assert connection["allowed_endpoints"] == [BODY["endpoint"]]
    oauth = connection["configuration"]["credentialProvider"]["oauthCredentialProvider"]
    assert oauth["grantType"] == grant and oauth["scopes"] == ["read"]
    assert "/oauth2credentialprovider/test-studio-mcp-oauth-" in oauth["providerArn"]
    if grant == "AUTHORIZATION_CODE":
        assert oauth["defaultReturnUrl"] == "https://studio.example.com/oauth/callback"
        assert connection["user_authorization"]["mode"] == "gateway"
    else:
        assert "user_authorization" not in connection
        assert "defaultReturnUrl" not in oauth
    path = "/api/admin/mcp/auth-connections/" + value["connection_id"]
    info = setup[0].get(path).json()
    assert info["can_delete"] and not info["can_edit"]
    assert setup[0].post(path + "/delete", json={
        "expected_revision": 1, "confirm_name": BODY["name"], "idempotency_key": "delete-managed-oauth-0001",
    }).json()["phase"] == "DELETED"
    assert cloud.writes == ["secret", "provider", "provider_delete", "secret_delete"]


def test_oauth_lost_response_reconciles_without_repeating_secret_or_provider_creation(setup):
    cloud = managed(setup)
    cloud.lose = "provider"
    value = setup[0].post("/api/admin/mcp/credentials", json=BODY).json()
    assert value["phase"] == "NEEDS_RECONCILIATION"
    assert BODY["secret"] not in json.dumps(value)
    assert setup[0].get("/api/admin/mcp/credentials/" + BODY["idempotency_key"]).json()["phase"] == "READY"
    assert cloud.writes == ["secret", "provider"]
    assert setup[0].post("/api/admin/mcp/credentials", json={**BODY, "grant_type": "CLIENT_CREDENTIALS"}).status_code == 409


def test_client_credentials_discovery_needs_no_user_authorization_metadata(setup):
    from backend.mcp_credentials import oauth_provider_config
    cloud = managed(setup)
    body = {k: v for k, v in BODY.items()
            if k not in ("issuer", "authorization_endpoint", "token_endpoint")}
    body.update(grant_type="CLIENT_CREDENTIALS",
                discovery_url="https://identity.example.com/.well-known/openid-configuration")
    response = setup[0].post("/api/admin/mcp/credentials", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["phase"] == "READY"
    assert setup[0].post("/api/admin/mcp/credentials", json=body).json() == response.json()
    assert cloud.writes == ["secret", "provider"]
    with setup[1].tx() as db:
        state = setup[2].credentials.load(db, {"id": "admin"}, body["idempotency_key"])
        connection = get(db, "mcp-auth:" + response.json()["connection_id"])
    assert oauth_provider_config(state)["customOauth2ProviderConfig"]["oauthDiscovery"] == {
        "discoveryUrl": body["discovery_url"]}
    assert "user_authorization" not in connection
    provider = connection["configuration"]["credentialProvider"]["oauthCredentialProvider"]
    assert provider["grantType"] == "CLIENT_CREDENTIALS" and "defaultReturnUrl" not in provider
    assert BODY["secret"] not in json.dumps(state) + response.text


@pytest.mark.parametrize("patch", [
    {"discovery_url": "http://identity.example.com/discovery"},
    {"discovery_url": "https://127.0.0.1/discovery"},
    {"grant_type": "AUTHORIZATION_CODE"},
    {"authorization_endpoint": BODY["authorization_endpoint"]},
])
def test_discovery_metadata_is_service_only_exclusive_and_https(setup, patch):
    cloud = managed(setup)
    body = {k: v for k, v in BODY.items()
            if k not in ("issuer", "authorization_endpoint", "token_endpoint")}
    body.update(grant_type="CLIENT_CREDENTIALS",
                discovery_url="https://identity.example.com/.well-known/openid-configuration")
    response = setup[0].post("/api/admin/mcp/credentials", json={**body, **patch})
    assert response.status_code == 422
    assert not cloud.writes
    assert BODY["secret"] not in response.text


@pytest.mark.parametrize("grant", ["AUTHORIZATION_CODE", "CLIENT_CREDENTIALS"])
def test_optional_discovery_preserves_legacy_request_digest(setup, grant):
    from foundation_harness.config import digest
    managed(setup)
    body = {**BODY, "grant_type": grant}
    assert setup[0].post("/api/admin/mcp/credentials", json=body).status_code == 200
    with setup[1].tx() as db:
        state = setup[2].credentials.load(db, {"id": "admin"}, body["idempotency_key"])
    assert state["request_digest"] == digest({k: v for k, v in body.items()
                                              if k not in ("secret", "idempotency_key")})


@pytest.mark.parametrize("patch", [
    {"grant_type": "password"}, {"scopes": ["read", "read"]},
    {"token_endpoint": "http://localhost/token"}, {"authorization_endpoint": "https://127.0.0.1/authorize"},
    {"issuer": "https://identity.example.com/#" + API_BODY["secret"]},
    {"client_authentication_method": "NONE"}, {"secret": {"invalid": API_BODY["secret"]}},
])
def test_invalid_oauth_configuration_does_not_dispatch_or_echo_secrets(setup, patch):
    cloud = managed(setup)
    response = setup[0].post("/api/admin/mcp/credentials", json={**BODY, **patch})
    assert response.status_code == 422
    assert BODY["secret"] not in response.text
    assert not cloud.writes


@pytest.mark.parametrize("discovery", [False, True])
def test_native_managed_oauth_uses_external_secret_and_verifies_exact_provider_configuration(discovery):
    from backend.mcp_credentials import CredentialCloud as NativeCloud
    cloud = NativeCloud({"region": "us-west-2", "account": "123456789012"},
        session=boto3.Session(aws_access_key_id="test", aws_secret_access_key="test", region_name="us-west-2"))
    sid = "a" * 32
    state = {**{k: v for k, v in BODY.items() if k not in ("secret", "idempotency_key")},
        "id": sid, "deployment_prefix": "test-studio", "provider_name": "test-studio-mcp-oauth-" + sid,
        "secret_arn": "arn:aws:secretsmanager:us-west-2:123456789012:secret:test-studio/mcp/" + sid + "-Ab1234"}
    provider = ("arn:aws:bedrock-agentcore:us-west-2:123456789012:"
                "token-vault/default/oauth2credentialprovider/" + state["provider_name"])
    config = {"oauthDiscovery": {"authorizationServerMetadata": {
        "issuer": BODY["issuer"], "authorizationEndpoint": BODY["authorization_endpoint"],
        "tokenEndpoint": BODY["token_endpoint"]}},
        "clientId": BODY["client_id"], "clientAuthenticationMethod": "CLIENT_SECRET_POST"}
    if discovery:
        for field in ("issuer", "authorization_endpoint", "token_endpoint"):
            state.pop(field)
        state.update(grant_type="CLIENT_CREDENTIALS",
                     discovery_url="https://identity.example.com/.well-known/openid-configuration")
        config["oauthDiscovery"] = {"discoveryUrl": state["discovery_url"]}
    native = {"name": state["provider_name"], "credentialProviderArn": provider,
        "clientSecretArn": {"secretArn": state["secret_arn"]}, "clientSecretJsonKey": "credential",
        "clientSecretSource": "EXTERNAL", "status": "READY",
        "oauth2ProviderConfigOutput": {"customOauth2ProviderConfig": config},
        "callbackUrl": "https://bedrock-agentcore.us-west-2.amazonaws.com/identities/oauth2/callback"}
    tags = cloud.tags(state)
    with Stubber(cloud.control) as control:
        control.add_response("create_oauth2_credential_provider", native, {
            "name": state["provider_name"], "credentialProviderVendor": "CustomOauth2",
            "oauth2ProviderConfigInput": {"customOauth2ProviderConfig": {
                **config, "clientSecretSource": "EXTERNAL",
                "clientSecretConfig": {"secretId": state["secret_arn"], "jsonKey": "credential"}}},
            "tags": tags})
        control.add_response("get_oauth2_credential_provider", {
            **native, "credentialProviderVendor": "CustomOauth2", "createdTime": 0, "lastUpdatedTime": 0},
            {"name": state["provider_name"]})
        control.add_response("list_tags_for_resource", {"tags": tags}, {"resourceArn": provider})
        cloud.create_provider(state)
        assert cloud.read_provider(state) == provider
        assert state["callback_url"] == native["callbackUrl"]
        changed = copy.deepcopy(native)
        changed_discovery = changed["oauth2ProviderConfigOutput"]["customOauth2ProviderConfig"]["oauthDiscovery"]
        if discovery:
            changed_discovery["discoveryUrl"] = "https://elsewhere.example.com/discovery"
        else:
            changed_discovery["authorizationServerMetadata"]["tokenEndpoint"] = "https://elsewhere.example.com/token"
        control.add_response("get_oauth2_credential_provider", {
            **changed, "credentialProviderVendor": "CustomOauth2", "createdTime": 0, "lastUpdatedTime": 0},
            {"name": state["provider_name"]})
        with pytest.raises(ValueError, match="binding changed"):
            cloud.read_provider(state)
        control.assert_no_pending_responses()
