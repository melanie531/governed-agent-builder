"""Secretless, exact-endpoint IAM connections for customer AgentCore Runtimes."""
import re
import time
from urllib.parse import quote, unquote, urlsplit
from uuid import uuid4

from fastapi import HTTPException
from pydantic import Field

from foundation_harness.config import digest
from .foundation_runs import get, put
from .journey_schema import Strict
from .mcp_credentials import credential_prefix
from .mcp_management import DeleteConnection


def runtime_identity(endpoint, settings):
    region, account = settings["region"], settings["account"]
    host = f"bedrock-agentcore.{region}.amazonaws.com"
    url = urlsplit(endpoint)
    prefix, suffix = "/runtimes/", "/invocations"
    if not url.path.startswith(prefix) or not url.path.endswith(suffix):
        raise ValueError("Use an AgentCore Runtime invocation URL")
    arn = unquote(url.path[len(prefix):-len(suffix)])
    if not re.fullmatch(
            re.escape(f"arn:aws:bedrock-agentcore:{region}:{account}:runtime/")
            + r"[A-Za-z][A-Za-z0-9_]{0,47}-[A-Za-z0-9]+", arn):
        raise ValueError("Use a Runtime in this installation's account and region")
    canonical = f"https://{host}{prefix}{quote(arn, safe='')}{suffix}?qualifier=DEFAULT"
    if endpoint != canonical:
        raise ValueError("Use the canonical DEFAULT Runtime invocation URL")
    return {"arn": arn, "origin": "https://" + host, "region": region}


def validate_connection(connection, settings, prefix=None):
    endpoints = connection.get("allowed_endpoints", [])
    if len(endpoints) != 1:
        raise ValueError("IAM authentication must bind exactly one Runtime endpoint")
    identity = runtime_identity(endpoints[0], settings)
    expected = {"credentialProviderType": "GATEWAY_IAM_ROLE", "credentialProvider": {
        "iamCredentialProvider": {"service": "bedrock-agentcore", "region": identity["region"]}}}
    if (connection["configuration"] != expected
            or connection["allowed_origins"] != [identity["origin"]]):
        raise ValueError("Runtime IAM signing configuration changed")
    if "user_authorization" in connection:
        from .mcp_user_oauth import validate_user_configuration
        if not prefix:
            raise ValueError("User authorization requires an installation credential prefix")
        validate_user_configuration(connection["user_authorization"], settings, prefix)


class IamInput(Strict):
    name: str = Field(min_length=2, max_length=80)
    endpoint: str = Field(max_length=2048)
    user_authorization: bool = False
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class IamCredentials:
    def __init__(self, service):
        self.service = service

    @staticmethod
    def key(actor, token):
        return "mcp-auth-request:" + digest([actor["id"], "runtime-iam", token])

    @staticmethod
    def public(state):
        return {k: state[k] for k in ("id", "phase", "name", "connection_id", "runtime_arn", "endpoint", "user_authorization")
                if k in state}

    def read(self, actor, token):
        self.service.admin(actor)
        def read(db):
            state = get(db, self.key(actor, token))
            if not state or state.get("auth_type") != "GATEWAY_IAM_ROLE":
                raise HTTPException(404, "IAM authentication request not found")
            if state["deployment_prefix"] != credential_prefix(self.service.config(db)):
                raise HTTPException(409, "Authentication configuration changed")
            return self.public(state)
        return self.service.tx(read)

    def create(self, actor, value):
        self.service.admin(actor)
        try:
            body = IamInput.model_validate(value)
            identity = runtime_identity(body.endpoint, self.service.settings)
            if len(body.name.strip()) < 2:
                raise ValueError()
        except Exception:
            raise HTTPException(422, "Enter a name and the DEFAULT AgentCore Runtime invocation URL in this account and region") from None
        metadata = {"name": body.name.strip(), "endpoint": body.endpoint}
        if body.user_authorization:
            metadata["user_authorization"] = True
        prefix, prior = self.service.tx(lambda db: (
            credential_prefix(self.service.config(db)), get(db, self.key(actor, body.idempotency_key))))
        if prior:
            if prior["deployment_prefix"] != prefix or prior["request_digest"] != digest(metadata):
                raise HTTPException(409, "IAM authentication request changed; check its saved status")
            return self.public(prior)
        user_config = None
        if body.user_authorization:
            from .mcp_user_oauth import OAuthCloud
            if not self.service.auth:
                raise HTTPException(409, "Configure hosted Studio identity before enabling user authorization")
            cloud = getattr(self.service, "oauth_cloud", None) or OAuthCloud(self.service.settings)
            try:
                user_config = cloud.configuration(identity["arn"], prefix, self.service.auth)
            except Exception:
                raise HTTPException(422, "Runtime user authorization is not ready. Check its Studio identity, OAuth provider, workload, callback and deployment tags.") from None
        def save(db):
            config = self.service.config(db)
            if credential_prefix(config) != prefix:
                raise HTTPException(409, "Authentication configuration changed")
            key = self.key(actor, body.idempotency_key)
            prior = get(db, key)
            if prior:
                if prior["deployment_prefix"] != prefix or prior["request_digest"] != digest(metadata):
                    raise HTTPException(409, "IAM authentication request changed; check its saved status")
                return self.public(prior)
            if len(config["connections"]) >= 20:
                raise HTTPException(429, "Authentication connection limit reached")
            if any(body.endpoint in c.get("allowed_endpoints", []) for c in config["connections"]):
                raise HTTPException(409, "An IAM authentication connection already exists for this Runtime")
            sid = uuid4().hex
            cid = "iam-" + sid
            state = {"id": sid, **metadata, "connection_id": cid, "phase": "READY",
                     "auth_type": "GATEWAY_IAM_ROLE", "runtime_arn": identity["arn"],
                     "origin": identity["origin"], "deployment_prefix": prefix,
                     "request_digest": digest(metadata), "revision": 1, "created": time.time()}
            connection = {"id": cid, "name": metadata["name"], "allowed_origins": [identity["origin"]],
                          "allowed_endpoints": [body.endpoint],
                          "configuration": {"credentialProviderType": "GATEWAY_IAM_ROLE", "credentialProvider": {
                              "iamCredentialProvider": {"service": "bedrock-agentcore", "region": identity["region"]}}}}
            if user_config:
                connection["user_authorization"] = user_config
            put(db, key, state)
            put(db, "mcp-auth:" + cid, connection)
            self.service.audit(db, actor["id"], "mcp_iam_authentication_created", cid,
                               {"runtime_arn": identity["arn"]})
            return self.public(state)
        return self.service.tx(save)

    def delete(self, actor, cid, value):
        from .mcp_auth_management import native_state, references, unlocked
        self.service.admin(actor)
        try:
            body = DeleteConnection.model_validate(value)
        except Exception:
            raise HTTPException(422, "Confirm the IAM connection name and current revision") from None
        key = "mcp-auth-change:" + digest([actor["id"], cid, body.idempotency_key])
        request_digest = digest(["delete-iam", body.model_dump()])
        def remove(db):
            prior = get(db, key)
            if prior:
                if prior["digest"] != request_digest:
                    raise HTTPException(409, "Authentication deletion request changed")
                return prior["response"]
            native_key, native = native_state(db, cid)
            if (not native or native.get("auth_type") not in ("GATEWAY_IAM_ROLE", "OAUTH")
                    or native["phase"] != "READY" or native["revision"] != body.expected_revision
                    or native["deployment_prefix"] != credential_prefix(self.service.config(db))):
                raise HTTPException(409, "IAM connection changed; refresh its status")
            unlocked(db, cid)
            if references(db, cid):
                raise HTTPException(409, "Used by MCP connections. Delete those connections first.")
            if body.confirm_name != native["name"]:
                raise HTTPException(422, "Enter the exact authentication connection name")
            response = {"phase": "DELETED", "connection_id": cid, "token": body.idempotency_key}
            native.update(phase="DELETED", revision=native["revision"] + 1)
            put(db, native_key, native)
            db.delete("settings", where=[("key", "=", "mcp-auth:" + cid)])
            put(db, key, {**response, "digest": request_digest, "response": response})
            self.service.audit(db, actor["id"], "mcp_authentication_reference_deleted", cid,
                               {k: native[k] for k in ("runtime_arn", "provider_arn") if k in native})
            return response
        return self.service.tx(remove)
