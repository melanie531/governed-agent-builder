"""Origin-bound administrator credentials. Secret values never enter the repository."""
import json
import re
import time
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import HTTPException
from pydantic import Field, SecretStr

from foundation_harness.config import digest
from .foundation_runs import get, put
from .journey_schema import Strict

MAX_PROVIDER_RETRIES = 8


class CredentialInput(Strict):
    name: str = Field(min_length=2, max_length=80)
    endpoint: str = Field(max_length=2048)
    header: str = Field(pattern=r"^[A-Za-z0-9-]{1,100}$")
    prefix: str = Field(default="", max_length=30, pattern=r"^[A-Za-z0-9 _-]*$")
    secret: SecretStr
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class ProviderRetry(Strict):
    expected_attempt: int = Field(ge=0, le=MAX_PROVIDER_RETRIES - 1)


def failure_code(error):
    code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
    return code if code in {"AccessDenied", "AccessDeniedException", "ValidationException", "ConflictException",
        "ResourceNotFoundException", "ThrottlingException", "ServiceQuotaExceededException"} else type(error).__name__


def failure_context(error):
    """Extract only IAM action/resource identifiers, never service error prose."""
    response = getattr(error, "response", {})
    message = response.get("Error", {}).get("Message", "")
    return {
        "actions": sorted(set(re.findall(r"\b(?:bedrock-agentcore|agent-registry|secretsmanager|kms):[A-Z][A-Za-z]+", message))),
        "resources": sorted(set(re.findall(
            r"arn:aws:(?:bedrock-agentcore|agent-registry|secretsmanager|kms):[a-z0-9-]*:[0-9]{12}:[A-Za-z0-9/_*.-]+", message))),
        "request_id": response.get("ResponseMetadata", {}).get("RequestId"),
    }


def credential_prefix(config):
    prefix = config.get("credential_prefix", "")
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,45}", prefix):
        raise ValueError("Credential setup is not enabled")
    return prefix


class Credentials:
    def __init__(self, service, cloud=None):
        self.service = service
        self.cloud = cloud or CredentialCloud(service.settings)

    def key(self, actor, token):
        return "mcp-auth-request:" + digest([actor["id"], token])

    def load(self, db, actor, token):
        state = get(db, self.key(actor, token))
        if not state:
            raise HTTPException(404, "Authentication request not found")
        if credential_prefix(self.service.config(db)) != state["deployment_prefix"]:
            raise HTTPException(409, "Authentication configuration changed")
        return state

    @staticmethod
    def public(state):
        retries = state.get("provider_retries", 0)
        return {**{k: state[k] for k in ("id", "phase", "name", "origin", "connection_id", "failure_code", "failure_context") if k in state},
                "retry_count": retries, "retry_available": bool(
                    state["phase"] not in ("READY", "MANAGING", "DELETED") and state.get("retry_verified") and "provider" in state["operations"]
                    and state.get("secret_arn") and retries < MAX_PROVIDER_RETRIES)}

    def save(self, db, actor, token, state):
        put(db, self.key(actor, token), state)

    def create(self, actor, value):
        from .mcp_onboarding import endpoint_origin
        self.service.admin(actor)
        # The route deliberately returns a fixed validation error, never Pydantic
        # input fields (which can contain a credential).
        try:
            body = CredentialInput.model_validate(value)
            secret = body.secret.get_secret_value()
            if not 1 <= len(secret) <= 8192 or re.search(r"[\x00-\x1f\x7f]", secret):
                raise ValueError()
            if body.header.lower() in {"host", "cookie", "content-length", "transfer-encoding", "connection"}:
                raise ValueError()
            origin = endpoint_origin(body.endpoint)
        except Exception:
            raise HTTPException(422, "Enter a name, HTTPS endpoint, valid credential header and API key or PAT") from None
        metadata = body.model_dump(exclude={"secret", "idempotency_key"})
        def reserve(db):
            config = self.service.config(db)
            prefix = credential_prefix(config)
            prior = get(db, self.key(actor, body.idempotency_key))
            if prior:
                if prior["request_digest"] != digest(metadata):
                    raise HTTPException(409, "Authentication request changed; check its saved status")
                return prior, False
            requests = [r for r in db.select("settings") if r["key"].startswith("mcp-auth-request:")
                        and json.loads(r["body"])["phase"] != "DELETED"]
            if len(requests) >= 20 or len(config["connections"]) >= 20:
                raise HTTPException(429, "Authentication connection limit reached")
            sid = uuid4().hex
            state = {"id": sid, "name": body.name.strip(), "origin": origin, "header": body.header,
                     "prefix": body.prefix.strip(), "phase": "SAVING", "deployment_prefix": prefix,
                     "secret_name": prefix + "/mcp/" + sid, "provider_name": prefix + "-mcp-" + sid,
                     "request_digest": digest(metadata), "created": time.time(), "operations": {}}
            self.save(db, actor, body.idempotency_key, state)
            self.service.audit(db, actor["id"], "mcp_authentication_requested", sid,
                               {"name": state["name"], "origin": origin})
            return state, True
        try:
            state, fresh = self.service.tx(reserve)
        except ValueError:
            raise HTTPException(409, "Authentication setup is not configured") from None
        if not fresh:
            return self.read(actor, body.idempotency_key)
        return self.advance(actor, body.idempotency_key, secret)

    def intent(self, actor, token, operation):
        def mark(db):
            state = self.load(db, actor, token)
            if operation in state["operations"]:
                raise ValueError("Operation already dispatched")
            state["operations"][operation] = "INTENT"
            self.save(db, actor, token, state)
            return state
        return self.service.tx(mark)

    def read(self, actor, token):
        self.service.admin(actor)
        state = self.service.tx(lambda db: self.load(db, actor, token))
        if state["phase"] in ("READY", "MANAGING", "DELETED"):
            return self.public(state)
        state["retry_verified"] = False
        try:
            secret_arn = self.cloud.read_secret(state)
            if secret_arn:
                state["secret_arn"] = secret_arn
                provider = self.cloud.read_provider(state)
                if provider:
                    state["connection_id"] = "auth-" + state["id"]
                    state["phase"] = "READY"
                    state.pop("failure_code", None)
                    state.pop("failure_context", None)
                    connection = {"id": state["connection_id"], "name": state["name"],
                        "allowed_origins": [state["origin"]], "configuration": {
                            "credentialProviderType": "API_KEY", "credentialProvider": {"apiKeyCredentialProvider": {
                                "providerArn": provider, "credentialLocation": "HEADER",
                                "credentialParameterName": state["header"], "credentialPrefix": state["prefix"]}}}}
                    def finish(db):
                        current = self.load(db, actor, token)
                        if current["phase"] in ("READY", "MANAGING", "DELETED"):
                            return current
                        state["operations"] = current["operations"]
                        put(db, "mcp-auth:" + state["connection_id"], connection)
                        self.save(db, actor, token, state)
                        return state
                    return self.public(self.service.tx(finish))
                state["phase"] = "NEEDS_RECONCILIATION" if "provider" in state["operations"] else "CONTINUE"
                state["retry_verified"] = True
            else:
                state["phase"] = "NEEDS_RECONCILIATION"
        except Exception as error:
            state["phase"] = "NEEDS_RECONCILIATION"
            state["failure_code"] = failure_code(error)
            state["failure_context"] = failure_context(error)
        def update(db):
            current = self.load(db, actor, token)
            # A stale GET must never overwrite a newer dispatch or completion.
            if current["operations"] == state["operations"] and current["phase"] not in ("READY", "MANAGING", "DELETED"):
                self.save(db, actor, token, state)
            return self.public(get(db, self.key(actor, token)))
        return self.service.tx(update)

    def advance(self, actor, token, secret=None):
        self.service.admin(actor)
        try:
            state = self.service.tx(lambda db: self.load(db, actor, token))
            if secret is not None and "secret" not in state["operations"]:
                state = self.intent(actor, token, "secret")
                self.cloud.create_secret(state, secret)
            current = self.read(actor, token)
            if current["phase"] == "CONTINUE":
                state = self.intent(actor, token, "provider")
                self.cloud.create_provider(state)
            return self.read(actor, token)
        except Exception as error:
            # Provider/service exceptions may echo inputs. Never return or log them.
            return self.failed(actor, token, error)

    def failed(self, actor, token, error):
        def record(db):
            state = self.load(db, actor, token)
            if state["phase"] not in ("READY", "MANAGING", "DELETED"):
                state.update(phase="NEEDS_RECONCILIATION", failure_code=failure_code(error), failure_context=failure_context(error))
                self.save(db, actor, token, state)
                self.service.audit(db, actor["id"], "mcp_credential_setup_failed", state["id"], {
                    "failure_code": state["failure_code"], "failure_context": state["failure_context"],
                    "provider_retries": state.get("provider_retries", 0)})
            return self.public(state)
        return self.service.tx(record)

    def retry(self, actor, token, body):
        self.service.admin(actor)
        current = self.read(actor, token)
        if current["phase"] in ("READY", "MANAGING", "DELETED"):
            return current
        def reserve(db):
            state = self.load(db, actor, token)
            count = state.get("provider_retries", 0)
            if body.expected_attempt < count:
                return state, False
            if body.expected_attempt != count or not self.public(state)["retry_available"]:
                raise HTTPException(409, "Check the current authentication status before retrying")
            state["provider_retries"] = count + 1
            self.save(db, actor, token, state)
            self.service.audit(db, actor["id"], "mcp_provider_retry_requested", state["id"], {"attempt": count + 1})
            return state, True
        state, dispatch = self.service.tx(reserve)
        if not dispatch:
            return self.read(actor, token)
        try:
            self.cloud.create_provider(state)
            return self.read(actor, token)
        except Exception as error:
            return self.failed(actor, token, error)


class CredentialCloud:
    def __init__(self, settings, *, session=None):
        self.settings = settings
        session = session or boto3.Session(region_name=settings["region"])
        config = Config(retries={"total_max_attempts": 1}, connect_timeout=3, read_timeout=8)
        self.secrets = session.client("secretsmanager", config=config)
        self.control = session.client("bedrock-agentcore-control", config=config)

    @staticmethod
    def missing(call):
        try:
            return call()
        except ClientError as error:
            if error.response["Error"]["Code"] == "ResourceNotFoundException":
                return None
            raise

    @staticmethod
    def tags(state):
        return {"auto-delete": "no", "project": "governed-agent-builder",
                "deployment": state["deployment_prefix"], "mcp-auth-request": state["id"]}

    def secret_metadata(self, state, *, allow_deleted=False, version=None):
        value = self.missing(lambda: self.secrets.describe_secret(SecretId=state["secret_name"]))
        if not value:
            return None
        tags = {t["Key"]: t["Value"] for t in value.get("Tags", [])}
        expected = f"arn:aws:secretsmanager:{self.settings['region']}:{self.settings['account']}:secret:{state['secret_name']}-"
        if (value["Name"] != state["secret_name"] or not value["ARN"].startswith(expected)
                or (value.get("DeletedDate") and not allow_deleted)
                or any(tags.get(k) != v for k, v in self.tags(state).items())
                or (version and "AWSCURRENT" not in value.get("VersionIdsToStages", {}).get(version, []))):
            raise ValueError("Credential secret binding changed")
        return value

    def read_secret(self, state):
        value = self.secret_metadata(state, version=state.get("secret_version", state["id"]))
        return value["ARN"] if value else None

    def management_read(self, stage, state, operation):
        if stage == "rotate":
            secret = self.secret_metadata(state)
            if not secret:
                raise ValueError("Credential secret is missing")
            versions = secret.get("VersionIdsToStages", {})
            if "AWSCURRENT" in versions.get(operation["version"], []):
                return True
            if "AWSCURRENT" not in versions.get(state.get("secret_version", state["id"]), []):
                raise ValueError("Credential was changed outside this operation")
            return False
        if stage == "provider_delete":
            return self.read_provider(state) is None
        if stage == "secret_delete":
            secret = self.secret_metadata(state, allow_deleted=True)
            return not secret or bool(secret.get("DeletedDate"))
        raise ValueError("Unknown credential operation")

    def management_write(self, stage, state, operation, value=None):
        if stage == "rotate":
            if self.read_secret(state) != state["secret_arn"] or not self.read_provider(state):
                raise ValueError("Credential binding changed")
            return self.secrets.put_secret_value(SecretId=state["secret_arn"], ClientRequestToken=operation["version"],
                                                  SecretString=json.dumps({"credential": value}))
        if stage == "provider_delete":
            if self.read_provider(state):
                return self.control.delete_api_key_credential_provider(name=state["provider_name"])
            return None
        if stage == "secret_delete":
            if self.read_provider(state) is not None:
                raise ValueError("Credential provider still exists")
            if self.read_secret(state) != state["secret_arn"]:
                raise ValueError("Credential secret changed")
            return self.secrets.delete_secret(SecretId=state["secret_arn"], RecoveryWindowInDays=7)
        raise ValueError("Unknown credential operation")

    def create_secret(self, state, value):
        self.secrets.create_secret(Name=state["secret_name"], ClientRequestToken=state["id"],
            SecretString=json.dumps({"credential": value}),
            Tags=[{"Key": k, "Value": v} for k, v in self.tags(state).items()])

    def read_provider(self, state):
        value = self.missing(lambda: self.control.get_api_key_credential_provider(name=state["provider_name"]))
        if not value:
            return None
        expected = f"arn:aws:bedrock-agentcore:{self.settings['region']}:{self.settings['account']}:token-vault/default/apikeycredentialprovider/{state['provider_name']}"
        if (value["credentialProviderArn"] != expected or value["name"] != state["provider_name"]
                or value.get("apiKeySecretSource") != "EXTERNAL"
                or value.get("apiKeySecretArn") != {"secretArn": state["secret_arn"]}
                or value.get("apiKeySecretJsonKey") != "credential"):
            raise ValueError("Credential provider binding changed")
        tags = self.control.list_tags_for_resource(resourceArn=expected)["tags"]
        if any(tags.get(k) != v for k, v in self.tags(state).items()):
            raise ValueError("Credential provider ownership changed")
        return expected

    def create_provider(self, state):
        self.control.create_api_key_credential_provider(name=state["provider_name"],
            apiKeySecretSource="EXTERNAL",
            apiKeySecretConfig={"secretId": state["secret_arn"], "jsonKey": "credential"},
            tags=self.tags(state))
