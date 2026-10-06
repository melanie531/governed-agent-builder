"""Generic remote MCP onboarding with durable intents and explicit recovery."""
import copy
import ipaddress
import json
import re
import time
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from jsonschema import Draft202012Validator
from pydantic import Field, field_validator

from foundation_harness.config import digest
from .foundation_runs import get, put
from .journey_schema import Strict
from .live_catalog import grant_scope

STOPPED = {"REVIEW", "READY", "FAILED", "NEEDS_RECONCILIATION", "DELETED"}
STAGES = ["connect", "discover", "register", "review", "submit", "approve", "publish"]
PHASE = {"connect": "CONNECTING", "discover": "DISCOVERING", "register": "REGISTERING",
         "review": "REVIEW", "submit": "SUBMITTING", "approve": "APPROVING", "publish": "PUBLISHING",
         "retire_registry": "DELETING", "retire_target": "DELETING", "retire_catalog": "DELETING"}


class RetryCreate(Strict):
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")


def retry_available(state):
    operation = state.get("operations", {}).get(state["stage"], {})
    return (state["phase"] == "NEEDS_RECONCILIATION" and state["stage"] in ("connect", "register", "retire_registry", "retire_target")
            and operation.get("intent") is True and len(operation.get("retry_requests", [])) < 3)


def endpoint_origin(endpoint):
    value = urlsplit(endpoint)
    if (value.scheme != "https" or not value.hostname or value.username or value.password
            or value.fragment or value.port not in (None, 443)
            or value.query not in ("", "qualifier=DEFAULT")
            or re.search(r"[\s\\\x00-\x1f]", endpoint) or len(endpoint) > 2048):
        raise ValueError("Use an HTTPS MCP endpoint without credentials or token parameters")
    host = value.hostname
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) or "." not in host or ".." in host:
        raise ValueError("Use a fully qualified server hostname")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP literal endpoints are not supported")
    if host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Private endpoints require a separately configured private connection")
    return "https://" + host


def endpoint_allowed(connection, endpoint):
    return (endpoint_origin(endpoint) in connection["allowed_origins"]
            and ("allowed_endpoints" not in connection or endpoint in connection["allowed_endpoints"]))


def configuration(value, settings):
    config = copy.deepcopy(value)
    prefix = f"arn:aws:agent-registry:{settings['region']}:{settings['account']}:registry/"
    if (not config.get("enabled") or not re.fullmatch(r"[A-Za-z0-9_-]+", config.get("registry_id", ""))
            or config.get("registry_arn") != prefix + config["registry_id"]):
        raise ValueError("Native AWS Agent Registry is not configured")
    if not config.get("workspaces") or not set(config["workspaces"]) <= {"research", "operations"}:
        raise ValueError("MCP workspaces are not configured")
    ids = set()
    for connection in config.get("connections", []):
        if not re.fullmatch(r"[a-z0-9-]{1,60}", connection["id"]) or connection["id"] in ids:
            raise ValueError("Invalid credential connection")
        ids.add(connection["id"])
        if not connection.get("allowed_origins") or any(endpoint_origin(o) != o for o in connection["allowed_origins"]):
            raise ValueError("Credential connections require exact approved HTTPS origins")
        auth = connection["configuration"]
        kind = auth["credentialProviderType"]
        if "user_authorization" in connection and kind not in ("GATEWAY_IAM_ROLE", "OAUTH"):
            raise ValueError("Runtime user authorization requires an IAM Runtime connection")
        provider = auth.get("credentialProvider", {})
        vault = f"arn:aws:bedrock-agentcore:{settings['region']}:{settings['account']}:token-vault/default/"
        if kind == "API_KEY":
            p = provider["apiKeyCredentialProvider"]
            if (not p["providerArn"].startswith(vault + "apikeycredentialprovider/")
                    or p.get("credentialLocation") != "HEADER"
                    or not re.fullmatch(r"[A-Za-z0-9-]{1,100}", p["credentialParameterName"])
                    or re.search(r"[\r\n]", p.get("credentialPrefix", ""))):
                raise ValueError("Invalid API-key credential connection")
        elif kind == "OAUTH":
            p = provider["oauthCredentialProvider"]
            if connection.get("user_authorization"):
                from .mcp_gateway_oauth import validate_connection
                validate_connection(connection, config, settings)
            elif not p["providerArn"].startswith(vault + "oauth2credentialprovider/") or p.get("grantType") != "CLIENT_CREDENTIALS":
                raise ValueError("Use a configured machine-to-machine OAuth connection")
        elif kind == "GATEWAY_IAM_ROLE":
            from .mcp_iam import validate_connection
            validate_connection(connection, settings, config.get("credential_prefix"))
        else:
            raise ValueError("An authenticated credential connection is required")
    if len(ids) > 20 or (not ids and not config.get("credential_prefix")):
        raise ValueError("Configure credential setup or 1–20 credential connections")
    if config.get("credential_prefix"):
        from .mcp_credentials import credential_prefix
        credential_prefix(config)
    return config


def binding_digest(config, state):
    connection = next(c for c in config["connections"] if c["id"] == state["connection_id"])
    if connection.get("user_authorization", {}).get("mode") != "gateway":
        # Adding a user Gateway does not change a legacy IAM/API-key contract.
        config = {k: v for k, v in config.items() if k != "oauth_gateway"}
    if state.get("config_scope") != "connection":
        return digest(config)
    return digest({**{k: v for k, v in config.items() if k not in ("connections", "secret_arns", "credential_prefix")},
                   "connections": [c for c in config["connections"] if c["id"] == state["connection_id"]]})


class Onboard(Strict):
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=500)
    endpoint: str = Field(min_length=10, max_length=2048)
    connection_id: str = Field(min_length=1, max_length=60)
    workspaces: list[str] = Field(min_length=1, max_length=2)
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    tool_schema: list[dict] | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("name", "endpoint")
    @classmethod
    def trim(cls, value):
        if len(value.strip()) < 2:
            raise ValueError("Enter a name and endpoint")
        return value.strip()


class Publish(Strict):
    discovery_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    tools: list[str] = Field(min_length=1, max_length=20)
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class EditConnection(Onboard):
    expected_revision: int = Field(ge=1)


def discovered_tools(state, descriptors):
    if not isinstance(descriptors, list) or not 1 <= len(descriptors) <= 100:
        raise ValueError("Expected 1–100 discovered tools")
    if len(json.dumps(descriptors).encode()) > 180_000:
        raise ValueError("Tool catalog exceeds the review size limit")
    prefix, seen, result = (state["target_name"] + "___" if state.get("target_name") else ""), set(), []
    for descriptor in descriptors:
        qualified = descriptor["name"]
        name = qualified.removeprefix(prefix)
        if (not qualified.startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name) or name in seen):
            raise ValueError("Unexpected or duplicate discovered tool")
        seen.add(name)
        schema = descriptor.get("inputSchema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError("Each tool must declare an object input schema")
        def refs(value):
            if isinstance(value, dict):
                if "$ref" in value and not str(value["$ref"]).startswith("#"):
                    raise ValueError("External schema references are not supported")
                for child in value.values():
                    refs(child)
            elif isinstance(value, list):
                for child in value:
                    refs(child)
        refs(schema)
        Draft202012Validator.check_schema(schema)
        result.append({"name": name, "description": descriptor.get("description", "")[:4000],
                       "inputSchema": copy.deepcopy(schema)})
    return sorted(result, key=lambda t: t["name"])


class McpOnboarding:
    """Provider-neutral onboarding, authorization and durable publication."""

    def __init__(self, store, settings, cloud, *, hosted=False, auth=None):
        self.store, self.settings, self.cloud = store, settings, cloud
        self.hosted, self.auth = hosted, auth

    def tx(self, operation):
        for attempt in range(6):
            try:
                with self.store.tx() as db:
                    return operation(db)
            except HTTPException as exc:
                if exc.status_code != 409 or exc.detail != "Concurrent governance update; reload and retry" or attempt == 5:
                    raise
                time.sleep(.025 * (attempt + 1))

    @staticmethod
    def admin(actor):
        if actor["role"] != "admin":
            raise HTTPException(403, "Platform admin required")

    @staticmethod
    def audit(db, actor, action, sid, detail):
        db.insert("audit", {"actor": actor, "action": action, "resource": sid,
                           "detail": json.dumps(detail), "created": time.time()})

    def detail(self, actor, server_id):
        self.admin(actor)
        return self.tx(lambda db: self.public(self.load(db, server_id)))

    def config(self, db):
        value = get(db, "mcp-onboarding") or {}
        value["connections"] = value.get("connections", []) + [
            json.loads(r["body"]) for r in db.select("settings") if r["key"].startswith("mcp-auth:")]
        return configuration(value, self.settings)

    @staticmethod
    def load(db, server_id):
        value = get(db, "mcp-connection:" + server_id)
        if not value:
            raise HTTPException(404, "MCP connection not found")
        return value

    @staticmethod
    def public(state):
        fields = ("id", "job_id", "name", "description", "endpoint", "connection_id", "workspaces",
                  "phase", "tools", "selected_tools", "discovery_digest", "gateway_target_id",
                  "registry_record_id", "registry_record_arn", "catalog_id", "created", "updated", "error",
                  "failure_code", "failure_context", "revision", "history", "deleted_at", "schema_source")
        return {**{k: state[k] for k in fields if k in state}, "retry_available": retry_available(state)}

    def options(self, actor):
        self.admin(actor)
        try:
            config = self.tx(self.config)
        except (ValueError, KeyError, TypeError):
            return {"enabled": False, "connections": [], "workspaces": [],
                    "reason": "Configure the platform Registry and credential connections to enable onboarding."}
        return {"enabled": True, "workspaces": config["workspaces"], "credential_setup": bool(config.get("credential_prefix")),
                "runtime_iam_setup": bool(config.get("credential_prefix")),
                "oauth_setup": bool(config.get("credential_prefix")),
                "oauth_grants": (["AUTHORIZATION_CODE"] if config.get("oauth_gateway") else []) + ["CLIENT_CREDENTIALS"],
                "python_bundle": self.tx(lambda db: (get(db, "mcp-python-config") or {}).get("bundle_name")),
                "python_packages": bool(self.settings.get("mcp_package_upload")),
                "connections": [{"id": c["id"], "name": c["name"], "allowed_origins": c["allowed_origins"],
                                 "auth_type": c["configuration"]["credentialProviderType"],
                                 **({"callback_url": c["callback_url"]} if c.get("callback_url") else {}),
                                 **({"requires_schema": True} if c.get("user_authorization", {}).get("mode") == "gateway" else {})
                                 } for c in config["connections"]]}

    def list(self, actor):
        self.admin(actor)
        return self.tx(lambda db: {"items": sorted(
            [self.public(s) for r in db.select("settings") if r["key"].startswith("mcp-connection:")
             if (s := json.loads(r["body"]))["phase"] != "DELETED"],
            key=lambda x: x["created"], reverse=True)})

    def request(self, actor, token):
        self.admin(actor)
        def read(db):
            receipt = get(db, "mcp-onboard-request:" + digest([actor["id"], token]))
            if not receipt:
                raise HTTPException(404, "Onboarding request not found")
            return self.public(self.load(db, receipt["id"]))
        return self.tx(read)

    def job(self, db, state, actor, session_hash):
        job_id, now = uuid4().hex, time.time()
        state.update(job_id=job_id, requester=actor["id"], deadline=now + 900, claim=None, error=None)
        put(db, "mcp-onboarding-job:" + job_id, {"server_id": state["id"]})
        db.insert("jobs", {"id": job_id, "agent": "mcp-onboard:" + state["id"], "version": 1,
                          "requester": actor["id"], "idem": job_id, "stage": "QUEUED", "result": "{}",
                          "created": now, "updated": now, "deadline": now + 900, "attempts": 0})
        if session_hash:
            db.insert("job_authority", {"id": job_id, "session_hash": session_hash})
        self.persist(db, state)
        return {"id": state["id"], "job_id": job_id, "phase": state["phase"]}

    def create(self, actor, body, session_hash=None):
        self.admin(actor)
        payload = body.model_dump(exclude={"idempotency_key"}, exclude_none=True)
        def enqueue(db):
            key = "mcp-onboard-request:" + digest([actor["id"], body.idempotency_key])
            prior = get(db, key)
            if prior:
                if prior["digest"] != digest(payload):
                    raise HTTPException(409, "This request key belongs to another connection")
                return prior["response"]
            from .mcp_deployments import assert_endpoint_available
            assert_endpoint_available(db, body.endpoint)
            try:
                config = self.config(db)
                connection = next(c for c in config["connections"] if c["id"] == body.connection_id)
                from .mcp_auth_management import unlocked
                unlocked(db, body.connection_id)
                if not endpoint_allowed(connection, body.endpoint):
                    raise ValueError()
                if len(set(body.workspaces)) != len(body.workspaces) or not set(body.workspaces) <= set(config["workspaces"]):
                    raise ValueError()
                native_oauth = connection.get("user_authorization", {}).get("mode") == "gateway"
                if bool(body.tool_schema) != native_oauth:
                    raise ValueError("Supply the tool schema for authorization-code OAuth")
                if body.tool_schema:
                    normalized_schema = discovered_tools({"target_name": "schema"}, [
                        {**t, "name": "schema___" + t["name"]} for t in body.tool_schema])
            except (ValueError, KeyError, TypeError, StopIteration):
                raise HTTPException(422, "Choose a configured connection, its approved HTTPS endpoint, and permitted workspaces") from None
            existing = [s for r in db.select("settings") if r["key"].startswith("mcp-connection:")
                        if (s := json.loads(r["body"]))["phase"] != "DELETED"]
            if len(existing) >= 30:
                raise HTTPException(429, "MCP onboarding limit reached")
            if any(s["name"].casefold() == body.name.casefold() or s["endpoint"] == body.endpoint for s in existing):
                raise HTTPException(409, "This connection name or endpoint has already been onboarded")
            sid = uuid4().hex
            state = {"id": sid, **payload, "phase": "CONNECTING", "stage": "connect", "revision": 1,
                     "config_scope": "connection", "target_name": "studio-remote-" + sid[:12],
                     "catalog_id": "mcp-remote-" + sid, "created": time.time(), "operations": {}, "owner": actor["id"]}
            if native_oauth:
                state["schema_source"] = "supplied"
                state["tool_schema"] = normalized_schema
            state["config_digest"] = binding_digest(config, state)
            response = self.job(db, state, actor, session_hash)
            put(db, key, {"id": sid, "digest": digest(payload), "response": response})
            self.audit(db, actor["id"], "mcp_onboarding_requested", sid, payload)
            return response
        return self.tx(enqueue)

    def authority(self, db, state):
        config = self.config(db)
        if binding_digest(config, state) != state["config_digest"]:
            raise ValueError("Onboarding configuration changed")
        self.authorize_job(db, state)
        return config

    def authorize_job(self, db, state):
        if self.hosted:
            row = db.select("job_authority", where=[("id", "=", state["job_id"])]).fetchone()
            session = db.select("hosted_sessions", where=[("id_hash", "=", row["session_hash"])]).fetchone() if row else None
            if not session or session["expires"] <= time.time() or session["subject"] != state["requester"]:
                raise ValueError("Administrator session expired")
            claims = self.auth.verify(session["access_token"], "access")
            membership = self.auth.membership(claims, dict(session).get("active_group"))
            if claims["sub"] != state["requester"] or membership["role"] != "admin":
                raise ValueError("Administrator membership required")

    def persist(self, db, state):
        state["updated"] = time.time()
        put(db, "mcp-connection:" + state["id"], state)
        stage = {"REVIEW": "SUCCEEDED", "READY": "SUCCEEDED", "DELETED": "SUCCEEDED", "FAILED": "FAILED", "NEEDS_RECONCILIATION": "UNKNOWN"}.get(state["phase"], state["phase"])
        db.update("jobs", {"stage": stage, "updated": state["updated"], "result": json.dumps(self.public(state))},
                  where=[("id", "=", state["job_id"])])

    def publish_request(self, actor, sid, body, session_hash=None):
        self.admin(actor)
        def enqueue(db):
            state = self.load(db, sid)
            key = "mcp-publish-request:" + digest([actor["id"], sid, body.idempotency_key] +
                                                  ([state["revision"]] if state.get("revision", 1) > 1 else []))
            prior = get(db, key)
            if prior:
                if prior["digest"] != digest(body.model_dump()):
                    raise HTTPException(409, "Publication request changed")
                return prior["response"]
            if state["phase"] != "REVIEW" or body.discovery_digest != state.get("discovery_digest"):
                raise HTTPException(409, "Review the current discovered tools before publication")
            names = {t["name"] for t in state["tools"]}
            if len(set(body.tools)) != len(body.tools) or not set(body.tools) <= names:
                raise HTTPException(422, "Select discovered tools")
            if binding_digest(self.config(db), state) != state["config_digest"]:
                raise HTTPException(409, "Connection configuration changed; publication stopped")
            state.update(stage="submit", phase="SUBMITTING", selected_tools=sorted(body.tools))
            response = self.job(db, state, actor, session_hash)
            put(db, key, {"digest": digest(body.model_dump()), "response": response})
            self.audit(db, actor["id"], "mcp_publication_approved", sid,
                       {"discovery_digest": body.discovery_digest, "tools": body.tools, "workspaces": state["workspaces"]})
            return response
        return self.tx(enqueue)

    def reconcile(self, actor, sid, session_hash=None):
        self.admin(actor)
        def enqueue(db):
            state = self.load(db, sid)
            if state["phase"] != "NEEDS_RECONCILIATION" or (state.get("claim") or {}).get("expires", 0) > time.time():
                raise HTTPException(409, "This connection is not ready for reconciliation")
            state["phase"] = PHASE[state["stage"]]
            return self.job(db, state, actor, session_hash)
        return self.tx(enqueue)

    def retry_create(self, actor, sid, body, session_hash=None):
        self.admin(actor)
        def enqueue(db):
            key = "mcp-native-retry:" + digest([actor["id"], sid, body.job_id])
            prior = get(db, key)
            if prior:
                return prior
            state = self.load(db, sid)
            if (not retry_available(state) or state["job_id"] != body.job_id
                    or (state.get("claim") or {}).get("expires", 0) > time.time()):
                raise HTTPException(409, "This native create request is not available for retry")
            if binding_digest(self.config(db), state) != state["config_digest"]:
                raise HTTPException(409, "Connection configuration changed; retry stopped")
            operation = state["operations"][state["stage"]]
            operation.setdefault("retry_requests", []).append(
                {"previous_job_id": body.job_id, "actor": actor["id"], "requested_at": time.time()})
            operation["retry_requested"] = True
            state["phase"] = PHASE[state["stage"]]
            response = self.job(db, state, actor, session_hash)
            put(db, key, response)
            self.audit(db, actor["id"], "mcp_native_create_retry_requested", sid,
                       {"stage": state["stage"], "previous_job_id": body.job_id})
            return response
        return self.tx(enqueue)

    def step(self, job_id):
        token = uuid4().hex
        def claim(db):
            link = get(db, "mcp-onboarding-job:" + job_id)
            if not link:
                return None
            state = self.load(db, link["server_id"])
            if state["job_id"] != job_id or state["phase"] in STOPPED or (state.get("claim") or {}).get("expires", 0) > time.time():
                return None
            try:
                config = self.authority(db, state)
                if state["deadline"] < time.time():
                    raise ValueError("Deadline exceeded")
            except Exception:
                state.update(phase="NEEDS_RECONCILIATION" if state.get("change") else "FAILED",
                             claim=None, error="Authorization, configuration, or deadline changed.")
                self.persist(db, state)
                return None
            state["claim"] = {"token": token, "expires": time.time() + 360}
            self.persist(db, state)
            return state, config
        work = self.tx(claim)
        if not work:
            return
        state, config = work
        stage = state["stage"]
        def update(action, *, release=True):
            def save(db):
                current = self.load(db, state["id"])
                if current["job_id"] != job_id or (current.get("claim") or {}).get("token") != token:
                    raise ValueError("Onboarding lease changed")
                self.authority(db, current)
                action(db, current)
                if release:
                    current["claim"] = None
                self.persist(db, current)
            self.tx(save)
        def advance(db, current):
            following = STAGES[STAGES.index(stage) + 1]
            current.update(stage=following, phase=PHASE[following])
            current.pop("failure_code", None)
            current.pop("failure_context", None)
        try:
            if stage == "retire_catalog":
                from .mcp_management import finish
                update(lambda db, current: finish(self, db, current))
                return
            if stage in ("retire_registry", "retire_target"):
                operation = state["operations"].get(stage, {})
                receipt = self.cloud.read(stage, state, config)
                if not receipt["absent"] and not receipt.get("pending"):
                    if not operation.get("intent") or operation.get("retry_requested"):
                        def dispatch_delete(db, current):
                            previous = current["operations"].get(stage, {})
                            current["operations"][stage] = {
                                **previous, "intent": True, "status": "DISPATCHING", "retry_requested": False,
                                "dispatch_count": previous.get("dispatch_count", 0) + 1}
                        update(dispatch_delete, release=False)
                        self.cloud.write(stage, state, config)
                        update(lambda db, current: current["operations"][stage].update(status="ACKNOWLEDGED"), release=False)
                        operation = {"status": "ACKNOWLEDGED"}
                        receipt = self.cloud.read(stage, state, config)
                    elif operation.get("status") != "ACKNOWLEDGED":
                        raise ValueError("The retained deletion has no confirmed result")
                def retired(db, current):
                    if receipt["absent"]:
                        current["operations"].setdefault(stage, {}).update(status="COMPLETE", receipt=receipt)
                        current["stage"] = "retire_target" if stage == "retire_registry" else "retire_catalog"
                    current.pop("failure_code", None)
                    current.pop("failure_context", None)
                update(retired)
                return
            if stage == "discover":
                descriptors = self.cloud.discover(state, config)
                if descriptors is None:
                    update(lambda db, current: None)
                    return
                tools = discovered_tools(state, descriptors)
                update(lambda db, current: (current.update(tools=tools, discovery_digest=digest(tools)), advance(db, current)))
                return
            if stage == "publish":
                tools = discovered_tools(state, self.cloud.verify(state, config))
                if digest(tools) != state["discovery_digest"]:
                    raise ValueError("Discovered tool definitions changed")
                def finish(db, current):
                    if self.publish_catalog(db, current):
                        current.update(phase="READY")
                        self.audit(db, current["requester"], "mcp_connection_published", current["id"],
                                   {"catalog_id": current["catalog_id"], "record_arn": current["registry_record_arn"]})
                update(finish)
                return
            if stage in ("submit", "approve"):
                tools = discovered_tools(state, self.cloud.discover(state, config))
                if digest(tools) != state["discovery_digest"]:
                    raise ValueError("Review no longer matches the discovered tools")
            operation = state["operations"].get(stage, {})
            receipt = self.cloud.read(stage, state, config)
            if not operation.get("intent") or (receipt is None and operation.get("retry_requested")):
                if not operation.get("intent") and receipt is not None:
                    raise ValueError("Unexpected preexisting native operation")
                def dispatch(db, current):
                    previous = current["operations"].get(stage, {})
                    current["operations"][stage] = {
                        **previous, "intent": True, "status": "DISPATCHING", "retry_requested": False,
                        "dispatch_count": previous.get("dispatch_count", 1 if previous.get("intent") else 0) + 1}
                update(dispatch, release=False)
                self.cloud.write(stage, state, config)
                update(lambda db, current: current["operations"][stage].update(status="ACKNOWLEDGED"), release=False)
                operation = {**operation, "status": "ACKNOWLEDGED"}
                receipt = self.cloud.read(stage, state, config)
            if receipt is None:
                if operation.get("intent") and operation.get("status") != "ACKNOWLEDGED":
                    raise ValueError("The retained native request has no visible result")
                update(lambda db, current: None)
                return
            def complete(db, current):
                current["operations"][stage].update(status="COMPLETE", receipt=receipt)
                if stage == "connect":
                    current["gateway_target_id"] = receipt["target_id"]
                elif stage == "register":
                    current.update(registry_record_id=receipt["record_id"], registry_record_arn=receipt["record_arn"])
                advance(db, current)
            update(complete)
        except Exception as exc:
            from .mcp_credentials import failure_code, failure_context
            code, context = failure_code(exc), failure_context(exc)
            def fail(db):
                current = self.load(db, state["id"])
                if current["job_id"] != job_id or (current.get("claim") or {}).get("token") != token:
                    return
                current.update(phase="NEEDS_RECONCILIATION", claim=None,
                               failure_code=code, failure_context=context,
                               error="The connection needs a status check. No native request is retried automatically. "
                                     "Changed definitions require a new review. Service status: " + code)
                self.persist(db, current)
                self.audit(db, current["requester"], "mcp_onboarding_service_failure", current["id"],
                           {"stage": stage, "failure_code": code, "failure_context": context})
            self.tx(fail)

    def publish_catalog(self, db, state):
        base = {"catalog": "journey", "version": "1", "approved": True, "fixture": False, "external": True,
                "origin": "AWS Agent Registry", "provider": "Remote MCP", "protocol": "MCP",
                "execution_ready": True, "integration_ready": True, "supported": True, "requestable": True,
                "managed_by": "studio-mcp-onboarding", "discoverable_workspaces": state["workspaces"],
                "default_grant_workspaces": state["workspaces"], "approved_external_workspaces": state["workspaces"],
                "data_handling": "Selected tool arguments are sent to the configured remote MCP endpoint.",
                "registry": {"arn": state["registry_record_arn"], "version": "1.0.0",
                             "descriptor_type": "mcpServer", "endpoint": state["endpoint"],
                             "discovery_digest": state["discovery_digest"]}}
        config = self.config(db)
        connection = next(c for c in config["connections"] if c["id"] == state["connection_id"])
        user_auth = connection.get("user_authorization")
        from .mcp_gateway_oauth import gateway_for
        gateway = gateway_for(connection, config, self.settings)
        binding = {"type": "mcp-server", "gateway_id": gateway["gateway_id"], "target_id": state["gateway_target_id"]}
        if user_auth:
            binding["user_authorization"] = {"connection_id": state["connection_id"],
                                             "configuration_digest": digest(user_auth)}
        server = {**base, "id": state["catalog_id"], "name": state["name"], "description": state["description"],
                  "kind": "mcp_server", "binding": binding, "binding_digest": digest(binding), "default_tool_ids": []}
        items = [server]
        for tool in state["tools"]:
            if tool["name"] not in state["selected_tools"]:
                continue
            cid = "mcp-remote-tool-" + state["id"] + "-" + digest(tool["name"])[:12]
            binding = {"type": "mcp", "gateway_id": gateway["gateway_id"], "target_id": state["gateway_target_id"],
                       "name": state["target_name"] + "___" + tool["name"], "inputSchema": tool["inputSchema"],
                       "schema_digest": digest(tool["inputSchema"])}
            if user_auth:
                binding["user_authorization"] = True
                if user_auth.get("mode") == "gateway":
                    binding["gateway_auth"] = "COGNITO"
                    binding["gateway_url"] = gateway["gateway_url"]
            items.append({**base, **tool, "id": cid, "kind": "tool", "operation": tool["name"],
                          "parent_id": state["catalog_id"], "binding": binding, "binding_digest": digest(binding)})
            server["default_tool_ids"].append(cid)
        for item in items:
            item["registry"] = {**item["registry"], "binding_digest": item["binding_digest"]}
            if db.select("components", where=[("id", "=", item["id"])]).fetchone():
                raise ValueError("Catalog identity already exists")
        # Prepared grants cannot authorize a capability until its catalog record
        # exists. Retain their bounded IDs so an interrupted publication can be
        # resumed or explicitly retired by the normal management workflow.
        state["publication_catalog_ids"] = [item["id"] for item in items]
        grants = {(r["persona"], r["component"]) for r in db.select("grants")}
        scopes = {r["key"] for r in db.select("settings")}
        prepared = 0
        for row in db.select("principals"):
            actor = json.loads(row["body"])
            if (actor["role"] != "business" or actor["workspace"] not in state["workspaces"]
                    or actor.get("grant_initialization")):
                continue
            for item in items:
                scope = grant_scope(actor, item["id"])
                if (actor["id"], item["id"]) in grants and scope in scopes:
                    continue
                # At most 60 grant writes + 21 catalog records + job/state/audit
                # and the revision fence: always below DynamoDB's 100 actions.
                if prepared == 30:
                    return False
                db.insert("grants", {"persona": actor["id"], "component": item["id"]}, ignore=True)
                put(db, scope, True)
                prepared += 1
        # Re-read membership under the same revision fence on every batch.
        # Pending first-login grants pick up these defaults before activation.
        for item in items:
            db.insert("components", {"id": item["id"], "body": json.dumps(item)})
        return True


def router(service, who):
    from .mcp_credentials import ProviderRetry
    from . import mcp_management
    routes = APIRouter(prefix="/api/admin/mcp")
    # Existing registrations remain visible and manageable. No remote-object
    # creation, profile configuration or provisioning retry endpoint is exposed.
    @routes.get("/servers")
    def legacy_servers(request: Request):
        return mcp_management.legacy_list(service, who(request, True))
    @routes.get("/servers/{sid}")
    def legacy_server(sid: str, request: Request):
        return mcp_management.legacy_detail(service, who(request, True), sid)
    def credentials():
        if not getattr(service, "credentials", None):
            from .mcp_credentials import Credentials
            service.credentials = Credentials(service)
        return service.credentials
    def python():
        if not getattr(service, "python", None):
            from .mcp_python import PythonMcp
            service.python = PythonMcp(service)
        return service.python
    def packages():
        runtime = python()
        if not getattr(runtime, "packages", None):
            from .mcp_package import PackageUploads
            runtime.packages = PackageUploads(runtime)
        return runtime.packages
    async def package_body(request):
        try:
            return await request.json()
        except Exception:
            raise HTTPException(422, "Invalid package upload request") from None
    @routes.post("/packages", status_code=201)
    async def package_reserve(request: Request):
        actor = who(request, True)
        service.admin(actor)
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(packages().reserve, actor, await package_body(request))
    @routes.get("/package-requests/{token}")
    def package_request(token: str, request: Request):
        return packages().request(who(request, True), token)
    @routes.get("/packages/{sid}")
    def package_detail(sid: str, request: Request):
        return packages().detail(who(request, True), sid)
    @routes.post("/packages/{sid}/parts/{index}")
    async def package_part(sid: str, index: int, request: Request):
        actor = who(request, True)
        service.admin(actor)
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(packages().part, actor, sid, index, await package_body(request))
    @routes.post("/packages/{sid}/deploy", status_code=202)
    def package_deploy(sid: str, request: Request):
        result = packages().deploy(who(request, True), sid, session_hash(request))
        request.app.state.wake.set()
        return result
    @routes.get("/python")
    def python_list(request: Request):
        return python().list(who(request, True))
    def deployments():
        from .mcp_deployments import Deployments
        return Deployments(python())
    @routes.get("/deployments")
    def deployment_list(request: Request):
        return deployments().list(who(request, True))
    @routes.get("/deployments/{sid}")
    def deployment_detail(sid: str, request: Request):
        return deployments().detail(who(request, True), sid)
    @routes.post("/deployments/{sid}/delete", status_code=202)
    def deployment_delete(sid: str, body: mcp_management.DeleteConnection, request: Request):
        result = deployments().delete(who(request, True), sid, body, session_hash(request))
        request.app.state.wake.set()
        return result
    @routes.post("/deployments/{sid}/reconcile", status_code=202)
    def deployment_reconcile(sid: str, request: Request):
        result = python().resume(who(request, True), sid, session_hash(request))
        request.app.state.wake.set()
        return result
    @routes.post("/deployments/{sid}/retry", status_code=202)
    def deployment_retry(sid: str, body: RetryCreate, request: Request):
        result = python().resume(who(request, True), sid, session_hash(request), body.job_id)
        request.app.state.wake.set()
        return result
    @routes.get("/python/{sid}")
    def python_detail(sid: str, request: Request):
        return python().detail(who(request, True), sid)
    @routes.get("/python-requests/{token}")
    def python_request(token: str, request: Request):
        return python().request(who(request, True), token)
    @routes.post("/python", status_code=202)
    async def python_create(request: Request):
        actor = who(request, True)
        service.admin(actor)
        try:
            value = await request.json()
        except Exception:
            raise HTTPException(422, "Invalid Python upload") from None
        from starlette.concurrency import run_in_threadpool
        result = await run_in_threadpool(python().create, actor, value, session_hash(request))
        request.app.state.wake.set()
        return result
    @routes.post("/python/{sid}/reconcile", status_code=202)
    def python_reconcile(sid: str, request: Request):
        result = python().resume(who(request, True), sid, session_hash(request))
        request.app.state.wake.set()
        return result
    @routes.post("/python/{sid}/retry", status_code=202)
    def python_retry(sid: str, body: RetryCreate, request: Request):
        result = python().resume(who(request, True), sid, session_hash(request), body.job_id)
        request.app.state.wake.set()
        return result
    def auth_management():
        from .mcp_auth_management import AuthManagement
        return AuthManagement(credentials())
    @routes.post("/oauth-credentials")
    async def create_oauth_credential(request: Request):
        from .mcp_oauth_credentials import OAuthCredentials
        from starlette.concurrency import run_in_threadpool
        actor = who(request, True)
        service.admin(actor)
        return await run_in_threadpool(OAuthCredentials(service).create, actor, await request.json())
    @routes.get("/oauth-credentials/{token}")
    def oauth_credential_status(token: str, request: Request):
        from .mcp_oauth_credentials import OAuthCredentials
        return OAuthCredentials(service).read(who(request, True), token)
    @routes.post("/iam-credentials")
    async def create_iam_credential(request: Request):
        from .mcp_iam import IamCredentials
        actor = who(request, True)
        service.admin(actor)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(422, "Invalid IAM authentication request") from None
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(IamCredentials(service).create, actor, body)
    @routes.get("/iam-credentials/{token}")
    def iam_credential_status(token: str, request: Request):
        from .mcp_iam import IamCredentials
        return IamCredentials(service).read(who(request, True), token)
    @routes.get("/auth-connections")
    def auth_connections(request: Request):
        return auth_management().list(who(request, True))
    @routes.get("/auth-connections/{cid}")
    def auth_connection(cid: str, request: Request):
        return auth_management().detail(who(request, True), cid)
    @routes.post("/auth-connections/{cid}/edit")
    async def edit_auth(cid: str, request: Request):
        actor = who(request, True)
        service.admin(actor)
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(auth_management().change, actor, cid, await request.json(), "edit")
    @routes.post("/auth-connections/{cid}/delete")
    async def delete_auth(cid: str, request: Request):
        actor = who(request, True)
        service.admin(actor)
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(auth_management().change, actor, cid, await request.json(), "delete")
    @routes.get("/auth-connections/{cid}/operations/{token}")
    def auth_operation(cid: str, token: str, request: Request):
        return auth_management().status(who(request, True), cid, token)
    @routes.post("/auth-connections/{cid}/operations/{token}/continue")
    async def auth_continue(cid: str, token: str, request: Request):
        actor = who(request, True)
        service.admin(actor)
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(auth_management().resume, actor, cid, token, await request.json())
    @routes.post("/credentials")
    async def create_credential(request: Request):
        actor = who(request, True)
        service.admin(actor)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(422, "Invalid authentication request") from None
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(credentials().create, actor, body)
    @routes.get("/credentials/{token}")
    def credential_status(token: str, request: Request):
        return credentials().read(who(request, True), token)
    @routes.post("/credentials/{token}/continue")
    def continue_credential(token: str, request: Request):
        return credentials().advance(who(request, True), token)
    @routes.post("/credentials/{token}/retry")
    def retry_credential(token: str, body: ProviderRetry, request: Request):
        return credentials().retry(who(request, True), token, body)
    def session_hash(request):
        if not service.hosted:
            return None
        from .hosted_auth import SESSION_COOKIE, sha
        return sha(request.cookies.get(SESSION_COOKIE, ""))
    @routes.get("/management/{source}/{sid}")
    def management(source: str, sid: str, request: Request):
        return mcp_management.info(service, who(request, True), source, sid)
    @routes.post("/management/{source}/{sid}/edit", status_code=202)
    def edit_connection(source: str, sid: str, body: EditConnection, request: Request):
        result = mcp_management.begin(service, who(request, True), source, sid, body, "edit", session_hash(request))
        request.app.state.wake.set()
        return result
    @routes.post("/management/{source}/{sid}/delete", status_code=202)
    def delete_connection(source: str, sid: str, body: mcp_management.DeleteConnection, request: Request):
        result = mcp_management.begin(service, who(request, True), source, sid, body, "delete", session_hash(request))
        request.app.state.wake.set()
        return result
    @routes.get("/onboarding-options")
    def options(request: Request):
        return service.options(who(request, True))
    @routes.get("/onboarding")
    def connections(request: Request):
        return service.list(who(request, True))
    @routes.get("/onboarding-requests/{token}")
    def receipt(token: str, request: Request):
        return service.request(who(request, True), token)
    @routes.post("/onboarding", status_code=202)
    def create(body: Onboard, request: Request):
        result = service.create(who(request, True), body, session_hash(request))
        request.app.state.wake.set()
        return result
    @routes.get("/onboarding/{sid}")
    def detail(sid: str, request: Request):
        return service.detail(who(request, True), sid)
    @routes.post("/onboarding/{sid}/publish", status_code=202)
    def publish(sid: str, body: Publish, request: Request):
        result = service.publish_request(who(request, True), sid, body, session_hash(request))
        request.app.state.wake.set()
        return result
    @routes.post("/onboarding/{sid}/reconcile", status_code=202)
    def reconcile(sid: str, request: Request):
        result = service.reconcile(who(request, True), sid, session_hash(request))
        request.app.state.wake.set()
        return result
    @routes.post("/onboarding/{sid}/retry", status_code=202)
    def retry(sid: str, body: RetryCreate, request: Request):
        result = service.retry_create(who(request, True), sid, body, session_hash(request))
        request.app.state.wake.set()
        return result
    return routes
