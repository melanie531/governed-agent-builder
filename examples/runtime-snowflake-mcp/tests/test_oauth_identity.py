import secrets
import time
from uuid import uuid4

import boto3
from botocore.stub import Stubber
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import pytest

from snowflake_mcp.identity import OAuthSettings, UserAuthorization, USER_TOKEN_HEADER


def setup():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = OAuthSettings(
        region="us-east-1", issuer="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Test",
        client_id="studiotestclient", workload_name="test-runtime-users",
        provider_name="test-user-oauth", scopes=("refresh_token", "session:role:READER"))
    client = boto3.client("bedrock-agentcore", region_name="us-east-1",
                          aws_access_key_id=secrets.token_hex(10),
                          aws_secret_access_key=secrets.token_hex(20))
    auth = UserAuthorization(settings, client=client, signing_key=lambda token: key.public_key())

    def token(**override):
        return jwt.encode({"iss": settings.issuer, "sub": str(uuid4()), "iat": int(time.time()),
                           "exp": int(time.time()) + 300, "client_id": settings.client_id,
                           "token_use": "access", "scope": "openid", **override}, key, algorithm="RS256")
    return settings, client, auth, token


def test_each_user_retrieves_only_their_native_oauth_token():
    settings, client, auth, jwt_token = setup()
    with Stubber(client) as native:
        for _ in range(2):
            user_jwt, workload, oauth = jwt_token(), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            native.add_response("get_workload_access_token_for_jwt", {"workloadAccessToken": workload},
                                {"workloadName": settings.workload_name, "userToken": user_jwt})
            native.add_response("get_resource_oauth2_token", {"accessToken": oauth}, {
                "workloadIdentityToken": workload, "resourceCredentialProviderName": settings.provider_name,
                "scopes": list(settings.scopes), "oauth2Flow": "USER_FEDERATION"})
            assert auth.token({USER_TOKEN_HEADER.lower(): user_jwt}) == oauth
        native.assert_no_pending_responses()


@pytest.mark.parametrize("claims", [
    {"client_id": "anotherclient"}, {"iss": "https://untrusted.example"},
    {"exp": 1}, {"token_use": "id"}, {"scope": "unrelated"}, {"sub": ""},
])
def test_invalid_user_identity_never_reaches_native_token_service(claims):
    _, client, auth, jwt_token = setup()
    with Stubber(client):
        with pytest.raises(ValueError, match="Sign in to Studio"):
            auth.token({USER_TOKEN_HEADER: jwt_token(**claims)})


def test_missing_identity_has_no_shared_authentication_fallback():
    _, client, auth, _ = setup()
    with Stubber(client):
        with pytest.raises(ValueError, match="Sign in to Studio"):
            auth.token({})


def test_consent_required_does_not_expose_native_url_or_tokens():
    settings, client, auth, jwt_token = setup()
    user_jwt, workload = jwt_token(), secrets.token_urlsafe(32)
    with Stubber(client) as native:
        native.add_response("get_workload_access_token_for_jwt", {"workloadAccessToken": workload})
        native.add_response("get_resource_oauth2_token", {
            "authorizationUrl": "https://authorization.example/" + secrets.token_urlsafe(32),
            "sessionUri": "urn:ietf:params:oauth:request_uri:" + uuid4().hex})
        with pytest.raises(ValueError, match="Connect your account in Studio") as error:
            auth.token({USER_TOKEN_HEADER: user_jwt})
        assert workload not in str(error.value) and user_jwt not in str(error.value)
