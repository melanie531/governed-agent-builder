"""User-bound AgentCore Identity OAuth; no shared credentials or token cache."""
from dataclasses import dataclass
from functools import cached_property
import json
import os
import re

import boto3
from botocore.config import Config
import jwt

USER_TOKEN_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Studio-User-Token"
SIGN_IN = "Sign in to Studio before using this connection."
CONNECT = "Connect your account in Studio before using this connection."


@dataclass(frozen=True)
class OAuthSettings:
    region: str
    issuer: str
    client_id: str
    workload_name: str
    provider_name: str
    scopes: tuple[str, ...]

    def __post_init__(self):
        if (not re.fullmatch(r"[a-z]{2}-[a-z]+-\d", self.region)
                or not re.fullmatch(r"https://cognito-idp\." + re.escape(self.region)
                                    + r"\.amazonaws\.com/" + re.escape(self.region) + r"_[A-Za-z0-9]+", self.issuer)
                or not re.fullmatch(r"[a-z0-9]{1,128}", self.client_id)
                or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", name)
                       for name in (self.workload_name, self.provider_name))
                or not 1 <= len(self.scopes) <= 10 or len(set(self.scopes)) != len(self.scopes)
                or any(not isinstance(s, str) or not 1 <= len(s) <= 128 or re.search(r"\s", s)
                       for s in self.scopes)):
            raise ValueError("Configure the exact Studio identity and OAuth provider for this Runtime.")

    @classmethod
    def environment(cls):
        return cls(region=os.environ["OAUTH_AWS_REGION"], issuer=os.environ["STUDIO_TOKEN_ISSUER"],
                   client_id=os.environ["STUDIO_TOKEN_CLIENT_ID"],
                   workload_name=os.environ["OAUTH_WORKLOAD_NAME"],
                   provider_name=os.environ["OAUTH_PROVIDER_NAME"],
                   scopes=tuple(json.loads(os.environ["OAUTH_SCOPES"])))


class UserAuthorization:
    def __init__(self, settings, *, client=None, signing_key=None):
        self.settings, self._client = settings, client
        self.keys = jwt.PyJWKClient(settings.issuer + "/.well-known/jwks.json",
                                   cache_keys=False, lifespan=300, timeout=5)
        self.signing_key = signing_key or (lambda token: self.keys.get_signing_key_from_jwt(token).key)

    @cached_property
    def client(self):
        return self._client or boto3.client("bedrock-agentcore", region_name=self.settings.region,
            config=Config(retries={"total_max_attempts": 1}, connect_timeout=5, read_timeout=20))

    def token(self, headers):
        try:
            values = ([value for name, value in headers.items() if name.lower() == USER_TOKEN_HEADER.lower()]
                      if not hasattr(headers, "getlist") else headers.getlist(USER_TOKEN_HEADER))
            if len(values) != 1 or not isinstance(values[0], str) or not 1 <= len(values[0]) <= 4096:
                raise ValueError()
            user_token = values[0]
            claims = jwt.decode(user_token, self.signing_key(user_token), algorithms=["RS256"],
                issuer=self.settings.issuer, options={"verify_aud": False,
                    "require": ["iss", "sub", "iat", "exp", "client_id", "token_use"]})
            if (claims["client_id"] != self.settings.client_id or claims["token_use"] != "access"
                    or not isinstance(claims["sub"], str) or not claims["sub"]
                    or "openid" not in claims.get("scope", "").split()):
                raise ValueError()
        except Exception:
            raise ValueError(SIGN_IN) from None
        try:
            workload = self.client.get_workload_access_token_for_jwt(
                workloadName=self.settings.workload_name, userToken=user_token)["workloadAccessToken"]
            result = self.client.get_resource_oauth2_token(
                workloadIdentityToken=workload, resourceCredentialProviderName=self.settings.provider_name,
                scopes=list(self.settings.scopes), oauth2Flow="USER_FEDERATION")
            token = result.get("accessToken")
            if not isinstance(token, str) or not 1 <= len(token) <= 131072:
                raise ValueError()
            return token
        except Exception:
            raise ValueError(CONNECT) from None
