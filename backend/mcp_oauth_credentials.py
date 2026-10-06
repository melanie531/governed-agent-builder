"""Save an existing native OAuth provider reference; never accept client secrets."""
import json
import re
import time
from uuid import uuid4

from fastapi import HTTPException
from pydantic import Field

from foundation_harness.config import digest
from .foundation_runs import get, put
from .journey_schema import Strict
from .mcp_credentials import credential_prefix
from .mcp_gateway_oauth import gateway_configuration, validate_connection
from .mcp_iam import IamCredentials
from .mcp_onboarding import endpoint_origin
from .mcp_user_oauth import OAuthCloud


class OAuthInput(Strict):
    name: str = Field(min_length=2, max_length=80)
    endpoint: str = Field(max_length=2048)
    provider_arn: str = Field(max_length=1024)
    scopes: list[str] = Field(min_length=1, max_length=10)
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class OAuthCredentials(IamCredentials):
    @staticmethod
    def key(actor, token):
        return "mcp-auth-request:" + digest([actor["id"], "gateway-oauth", token])

    def read(self, actor, token):
        self.service.admin(actor)
        def read(db):
            state = get(db, self.key(actor, token))
            if not state or state.get("auth_type") != "OAUTH":
                raise HTTPException(404, "OAuth authentication request not found")
            if state["deployment_prefix"] != credential_prefix(self.service.config(db)):
                raise HTTPException(409, "Authentication configuration changed")
            return self.public(state)
        return self.service.tx(read)

    def create(self, actor, value):
        self.service.admin(actor)
        try:
            body = OAuthInput.model_validate(value)
            origin = endpoint_origin(body.endpoint)
            if len(body.name.strip()) < 2 or len(set(body.scopes)) != len(body.scopes):
                raise ValueError()
            if any(not re.fullmatch(r"\S{1,128}", s) for s in body.scopes):
                raise ValueError()
        except Exception:
            raise HTTPException(422, "Enter a name, HTTPS endpoint, existing OAuth provider ARN and scopes") from None
        config = self.service.tx(self.service.config)
        prefix = credential_prefix(config)
        metadata = body.model_dump(exclude={"idempotency_key"})
        metadata["name"] = body.name.strip()
        request_digest = digest(metadata)
        def prior(db):
            state = get(db, self.key(actor, body.idempotency_key))
            if state:
                if state["request_digest"] != request_digest or state["deployment_prefix"] != prefix:
                    raise HTTPException(409, "OAuth authentication request changed; check its saved status")
                return self.public(state)
        previous = self.service.tx(prior)
        if previous:
            return previous
        if not self.service.auth:
            raise HTTPException(409, "Hosted Cognito sign-in is required for user OAuth")
        try:
            gateway = gateway_configuration(config.get("oauth_gateway"), self.service.settings)
            if gateway["issuer"] != self.service.auth.issuer or gateway["client_id"] != self.service.auth.client_id:
                raise ValueError()
            name = body.provider_arn.rsplit("/", 1)[-1]
            cloud = getattr(self.service, "oauth_cloud", None) or OAuthCloud(self.service.settings)
            user = cloud.gateway_provider(name, prefix, self.service.auth, body.scopes)
            connection = {"id": "oauth-" + uuid4().hex, "name": metadata["name"], "allowed_origins": [origin],
                "allowed_endpoints": [body.endpoint], "user_authorization": user,
                "configuration": {"credentialProviderType": "OAUTH", "credentialProvider": {
                    "oauthCredentialProvider": {"providerArn": body.provider_arn, "scopes": body.scopes,
                        "grantType": "AUTHORIZATION_CODE", "defaultReturnUrl": user["return_url"]}}}}
            validate_connection(connection, config, self.service.settings)
        except Exception:
            raise HTTPException(422, "Check the Cognito Gateway, provider ARN, external secret, scopes and deployment tags") from None
        def save(db):
            previous = prior(db)
            if previous:
                return previous
            current = self.service.config(db)
            if credential_prefix(current) != prefix or current.get("oauth_gateway") != config["oauth_gateway"]:
                raise HTTPException(409, "Authentication configuration changed")
            # The provider GET precedes this transaction. Recheck its owner and
            # retained tombstone so deletion cannot race a new local reference.
            from .mcp_auth_management import unlocked
            for row in db.select("settings"):
                if row["key"].startswith("mcp-auth-request:"):
                    owner = json.loads(row["body"])
                    if (owner.get("auth_type") == "OAUTH" and owner.get("secret_name")
                            and owner.get("provider_name") == name):
                        if owner["phase"] != "READY" or owner["deployment_prefix"] != prefix:
                            raise HTTPException(409, "OAuth provider owner is changing or deleted; refresh authentication connections")
                        unlocked(db, owner["connection_id"])
            if len(current["connections"]) >= 20:
                raise HTTPException(429, "Authentication connection limit reached")
            if any(body.endpoint in c.get("allowed_endpoints", []) for c in current["connections"]):
                raise HTTPException(409, "Authentication already exists for this endpoint")
            state = {"id": connection["id"][6:], **metadata, "connection_id": connection["id"],
                "phase": "READY", "auth_type": "OAUTH", "deployment_prefix": prefix,
                "request_digest": request_digest, "revision": 1, "created": time.time()}
            put(db, self.key(actor, body.idempotency_key), state)
            put(db, "mcp-auth:" + connection["id"], connection)
            self.service.audit(db, actor["id"], "mcp_oauth_reference_created", connection["id"],
                               {"provider_arn": body.provider_arn})
            return self.public(state)
        return self.service.tx(save)
