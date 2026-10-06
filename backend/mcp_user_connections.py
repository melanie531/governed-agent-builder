"""Session-bound per-user consent for approved Runtime MCP connections."""
import hmac
import json
import re
import secrets
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from foundation_harness.config import digest
from .foundation_runs import get, put
from .journey_catalog import choices, resolve
from .journey_schema import Strict
from .mcp_credentials import credential_prefix
from .mcp_iam import runtime_identity
from .mcp_user_oauth import OAuthCloud

FLOW = "mcp-user-flow:"
REQUEST = "mcp-user-request:"
STATE = "mcp-user-state:"
GRANT = "mcp-user-grant:"
SESSION_GRANT = "mcp-user-session-grant:"
GATEWAY_REAUTH_SECONDS = 3600


class Start(Strict):
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    force_authentication: bool = False


class Complete(Strict):
    state: str | None = Field(default=None, min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    session_uri: str = Field(max_length=1024, pattern=r"^urn:ietf:params:oauth:request_uri:[a-zA-Z0-9-._~]+$")


def connected(db, owner, server_id, configuration_digest):
    grant = get(db, GRANT + digest([owner, server_id]))
    return bool(grant and grant["phase"] == "CONNECTED" and grant["configuration_digest"] == configuration_digest)


class UserConnections:
    def __init__(self, service):
        self.service = service

    @property
    def cloud(self):
        return getattr(self.service, "oauth_cloud", None) or OAuthCloud(self.service.settings)

    def session(self, request, actor):
        if actor["role"] != "business":
            raise HTTPException(403, "Switch to Business User to connect your own account")
        if not self.service.auth:
            raise HTTPException(409, "User authorization requires hosted Studio sign-in")
        claims, session = self.service.auth.session(request)
        if claims["sub"] != actor["id"]:
            raise HTTPException(403, "The signed-in identity changed")
        if claims.get("exp", time.time() + 600) <= time.time() + 60:
            raise HTTPException(401, "Your Studio session is about to expire. Sign in again before completing authorization.")
        return session["access_token"]

    def binding(self, db, actor, sid):
        item = resolve(db, actor, {"mcp_server": [sid]})[sid]
        binding = item["binding"].get("user_authorization")
        if not binding:
            raise HTTPException(409, "This connection does not require per-user authorization")
        config = self.service.config(db)
        connection = next((c for c in config["connections"] if c["id"] == binding["connection_id"]), None)
        user = (connection or {}).get("user_authorization")
        if not user or digest(user) != binding["configuration_digest"]:
            raise HTTPException(409, "Connection authorization changed; contact your platform administrator")
        gateway_id = None
        if user.get("mode") == "gateway":
            from .mcp_gateway_oauth import gateway_configuration
            gateway_id = gateway_configuration(config.get("oauth_gateway"), self.service.settings)["gateway_id"]
        return {"server_id": sid, "name": item["name"], "configuration": user,
                "configuration_digest": digest(user), "endpoint": connection["allowed_endpoints"][0],
                "prefix": credential_prefix(config), "gateway_id": gateway_id}

    def validate_native(self, binding):
        if binding["configuration"].get("mode") == "gateway":
            user = binding["configuration"]
            current = self.cloud.gateway_provider(user["provider_name"], binding["prefix"], self.service.auth, user["scopes"])
            if current != user:
                raise HTTPException(409, "Gateway OAuth provider configuration changed")
            return
        arn = runtime_identity(binding["endpoint"], self.service.settings)["arn"]
        current = self.cloud.configuration(arn, binding["prefix"], self.service.auth)
        if current != binding["configuration"]:
            raise HTTPException(409, "Runtime authorization configuration changed")

    def required(self, db, actor, definition, *, require_consent=True):
        bindings = []
        for sid in definition["mcp_servers"]:
            item = resolve(db, actor, {"mcp_server": [sid]})[sid]
            if not item["binding"].get("user_authorization"):
                continue
            binding = self.binding(db, actor, sid)
            if (require_consent and binding["configuration"].get("mode") != "gateway"
                    and not connected(db, actor["id"], sid, binding["configuration_digest"])):
                raise HTTPException(409, "Connect your account for " + binding["name"] + " in My connections before running this agent")
            bindings.append(binding)
        return bindings

    def gateway_reauthorization(self, db, actor, definition, session_hash=None):
        result = []
        for binding in self.required(db, actor, definition, require_consent=False):
            if binding["configuration"].get("mode") != "gateway":
                continue
            grant = get(db, SESSION_GRANT + digest([actor["id"], binding["server_id"], session_hash])
                        if session_hash else GRANT + digest([actor["id"], binding["server_id"]]))
            valid = (grant and grant.get("phase") == "CONNECTED"
                     and grant.get("configuration_digest") == binding["configuration_digest"]
                     and type(grant.get("checked_at")) in (int, float))
            due_at = grant["checked_at"] + GATEWAY_REAUTH_SECONDS if valid else 0
            result.append({"server_id": binding["server_id"], "due": due_at <= time.time(),
                           "due_at": due_at})
        return result

    def preopen_provider_tab(self, db, actor, definition):
        return any(item["due"] for item in self.gateway_reauthorization(db, actor, definition))

    def capture_gateway(self, db, actor, definition, challenge, *, source_job_id=None):
        """Only called for a native challenge returned by the private agent invocation."""
        tool = next((t for t in resolve(db, actor, {"tool": definition["tools"]}).values()
                     if t["binding"]["name"] == challenge.get("tool_name")
                     and t["binding"].get("gateway_auth") == "COGNITO"), None)
        if not tool or tool["parent_id"] not in definition["mcp_servers"]:
            raise ValueError("Authorization challenge is not for a selected tool")
        binding = self.binding(db, actor, tool["parent_id"])
        if binding["configuration"].get("mode") != "gateway":
            raise ValueError("Unexpected Gateway authorization challenge")
        uri, url = challenge.get("session_uri", ""), challenge.get("authorization_url", "")
        parsed = urlsplit(url)
        if (not re.fullmatch(r"urn:ietf:params:oauth:request_uri:[A-Za-z0-9-._~]{1,900}", uri)
                or len(url) > 8192 or parsed.scheme != "https" or parsed.fragment
                or parsed.netloc != f"bedrock-agentcore.{self.service.settings['region']}.amazonaws.com"
                or parsed.path != "/identities/oauth2/authorize"
                or parse_qs(parsed.query).get("request_uri") != [uri]):
            raise ValueError("Invalid native Gateway authorization URL")
        key = STATE + "gateway:" + digest(uri)
        old = get(db, key)
        if old:
            flow, _ = self.owned(db, actor, old)
            if source_job_id and flow.get("source_job_id") not in (None, source_job_id):
                raise HTTPException(409, "Provider authorization belongs to another question")
            return self.public(flow)
        active = [json.loads(r["body"]) for r in db.select("settings") if r["key"].startswith(FLOW)]
        if sum(f["owner"] == actor["id"] and f["expires"] > time.time() for f in active) >= 10:
            raise HTTPException(429, "Too many recent consent requests; wait before running another attempt")
        flow_id = uuid4().hex
        flow = {"id": flow_id, "mode": "gateway", "owner": actor["id"], "server_id": binding["server_id"],
                "name": binding["name"], "configuration_digest": binding["configuration_digest"],
                "phase": "CONSENT_REQUIRED", "expires": time.time() + 600,
                "session_uri": uri, "authorization_url": url}
        if source_job_id:
            flow["source_job_id"] = source_job_id
        put(db, FLOW + flow_id, flow)
        put(db, key, flow_id)
        self.service.audit(db, actor["id"], "mcp_gateway_consent_required", binding["server_id"], {"flow_id": flow_id})
        return self.public(flow)

    @staticmethod
    def public(flow):
        phase = flow["phase"]
        if phase in ("INITIATING", "COMPLETING", "CHECKING"):
            phase = "NEEDS_CHECK"
        result = {"id": flow["id"], "server_id": flow["server_id"], "name": flow["name"], "phase": phase}
        if flow.get("mode"):
            result["mode"] = flow["mode"]
        if phase == "CONSENT_REQUIRED":
            result["authorization_url"] = flow["authorization_url"]
        return result

    def owned(self, db, actor, flow_id):
        flow = get(db, FLOW + flow_id)
        if not flow or flow["owner"] != actor["id"]:
            raise HTTPException(404, "Authorization request not found")
        binding = self.binding(db, actor, flow["server_id"])
        if binding["configuration_digest"] != flow["configuration_digest"]:
            raise HTTPException(409, "The connection changed during authorization")
        return flow, binding

    def list(self, request, actor):
        self.session(request, actor)
        def load(db):
            result = []
            for item in choices(db, actor):
                if item["kind"] != "mcp_server" or not item.get("granted"):
                    continue
                try:
                    binding = self.binding(db, actor, item["id"])
                except HTTPException as exc:
                    if exc.status_code == 409:
                        continue
                    raise
                result.append({"id": item["id"], "name": item["name"],
                    **({"mode": "gateway"} if binding["configuration"].get("mode") == "gateway" else {}),
                    "phase": "CONNECTED" if connected(db, actor["id"], item["id"], binding["configuration_digest"]) else "NOT_CONNECTED"})
            return {"items": result}
        return self.service.tx(load)

    def read(self, request, actor, flow_id=None, token=None):
        self.session(request, actor)
        def load(db):
            sid = flow_id or get(db, REQUEST + digest([actor["id"], token]))
            if not sid:
                raise HTTPException(404, "Authorization request not found")
            flow, _ = self.owned(db, actor, sid)
            if flow["expires"] <= time.time() and flow["phase"] != "CONNECTED":
                return {**self.public(flow), "phase": "EXPIRED"}
            return self.public(flow)
        return self.service.tx(load)

    def finish(self, request, actor, flow_id, phase, *, expected, **fields):
        self.session(request, actor)
        def commit(db):
            flow, binding = self.owned(db, actor, flow_id)
            if flow["phase"] != expected:
                return self.public(flow)
            if flow["expires"] <= time.time():
                raise HTTPException(410, "Authorization expired; start a new connection attempt")
            flow.update(phase=phase, **fields)
            put(db, FLOW + flow_id, flow)
            if flow.get("mode") == "gateway" and phase == "CONSENT_REQUIRED":
                key = STATE + "gateway:" + digest(fields["session_uri"])
                previous_id = get(db, key)
                if previous_id and previous_id != flow_id:
                    raise HTTPException(409, "This provider authorization session belongs to another request")
                put(db, key, flow_id)
            if phase == "CONNECTED":
                grant = {"phase": phase, "configuration_digest": binding["configuration_digest"],
                         "checked_at": time.time()}
                if flow.get("mode") == "gateway":
                    from .hosted_auth import SESSION_COOKIE, sha
                    cookie = request.cookies.get(SESSION_COOKIE)
                    if cookie:
                        put(db, SESSION_GRANT + digest([actor["id"], flow["server_id"], sha(cookie)]), grant)
                put(db, GRANT + digest([actor["id"], flow["server_id"]]), grant)
            self.service.audit(db, actor["id"], "mcp_user_authorization_" + phase.lower(), flow["server_id"], {"flow_id": flow_id})
            return self.public(flow)
        return self.service.tx(commit)

    def start(self, request, actor, sid, value):
        user_token = self.session(request, actor)
        try:
            body = Start.model_validate(value)
        except Exception:
            raise HTTPException(422, "Provide a valid authorization request key") from None
        request_key = REQUEST + digest([actor["id"], body.idempotency_key])
        signature = digest([sid, body.force_authentication])
        def previous(db):
            previous_id = get(db, request_key)
            if not previous_id:
                return None
            flow, _ = self.owned(db, actor, previous_id)
            if flow["request_digest"] != signature:
                raise HTTPException(409, "This authorization request key has different inputs")
            return self.public(flow)
        prior = self.service.tx(previous)
        if prior:
            return prior
        binding = self.service.tx(lambda db: self.binding(db, actor, sid))
        gateway = binding["gateway_id"] is not None
        if gateway:
            raise HTTPException(409, "Gateway provider consent must start from an agent's Gateway MCP tool call")
        try:
            self.validate_native(binding)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(409, "Runtime authorization is unavailable; contact your platform administrator") from None
        nonce, flow_id = secrets.token_urlsafe(32), uuid4().hex
        def reserve(db):
            prior = previous(db)
            if prior:
                return prior
            current = self.binding(db, actor, sid)
            if current != binding:
                raise HTTPException(409, "Connection authorization changed")
            active = [json.loads(r["body"]) for r in db.select("settings") if r["key"].startswith(FLOW)]
            if sum(f["owner"] == actor["id"] and f["expires"] > time.time() for f in active) >= 10:
                raise HTTPException(429, "Too many recent authorization attempts; wait before starting another")
            flow = {"id": flow_id, "owner": actor["id"], "server_id": sid, "name": binding["name"],
                    "configuration_digest": binding["configuration_digest"], "request_digest": signature,
                    "state_hash": digest(nonce), "phase": "INITIATING", "expires": time.time() + 600}
            put(db, FLOW + flow_id, flow)
            put(db, request_key, flow_id)
            put(db, STATE + digest(nonce), flow_id)
            if body.force_authentication:
                db.delete("settings", where=[("key", "=", GRANT + digest([actor["id"], sid]))])
            return None
        prior = self.service.tx(reserve)
        if prior:
            return prior
        try:
            flow_args = {"resourceOauth2ReturnUrl": binding["configuration"]["return_url"],
                         "forceAuthentication": body.force_authentication}
            flow_args["customState"] = nonce
            result = self.cloud.token(user_token, binding["configuration"], **flow_args)
            if result.get("accessToken"):
                return self.finish(request, actor, flow_id, "CONNECTED", expected="INITIATING")
            uri, url = result.get("sessionUri", ""), result.get("authorizationUrl", "")
            parsed = urlsplit(url)
            native_hosts = {"bedrock-agentcore.amazonaws.com",
                f"bedrock-agentcore.{self.service.settings['region']}.amazonaws.com",
                f"bedrock-agentcore-identity.{self.service.settings['region']}.amazonaws.com"}
            origin = parsed.scheme + "://" + parsed.netloc
            if (not re.fullmatch(r"urn:ietf:params:oauth:request_uri:[a-zA-Z0-9-._~]+", uri)
                    or parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment
                    or parsed.port not in (None, 443)
                    or not (origin == binding["configuration"]["authorization_origin"] or parsed.hostname in native_hosts)):
                raise ValueError()
            return self.finish(request, actor, flow_id, "CONSENT_REQUIRED", expected="INITIATING", session_uri=uri, authorization_url=url)
        except Exception:
            return self.finish(request, actor, flow_id, "NEEDS_CHECK", expected="INITIATING")

    def complete(self, request, actor, value):
        user_token = self.session(request, actor)
        try:
            body = Complete.model_validate(value)
        except Exception:
            raise HTTPException(422, "The authorization callback is invalid") from None
        def reserve(db):
            flow_id = get(db, STATE + digest(body.state) if body.state else STATE + "gateway:" + digest(body.session_uri))
            flow = get(db, FLOW + flow_id) if flow_id else None
            if not flow:
                raise HTTPException(404, "Authorization request not found")
            if flow["owner"] != actor["id"]:
                raise HTTPException(403, "Use the same Studio user who started authorization")
            flow, binding = self.owned(db, actor, flow_id)
            if (flow.get("session_uri") != body.session_uri or
                    (flow.get("mode") != "gateway" and (
                        not body.state or not hmac.compare_digest(flow["state_hash"], digest(body.state))))
                    or (flow.get("mode") == "gateway" and body.state is not None)):
                raise HTTPException(403, "Authorization state or native session does not match")
            if flow["expires"] <= time.time():
                raise HTTPException(410, "Authorization expired; start a new connection attempt")
            if flow["phase"] != "CONSENT_REQUIRED":
                return flow, binding, False
            flow["phase"] = "COMPLETING"
            flow["completion_attempted"] = True
            put(db, FLOW + flow_id, flow)
            return flow, binding, True
        flow, binding, dispatch = self.service.tx(reserve)
        if not dispatch:
            return self.public(flow)
        try:
            self.validate_native(binding)
            self.cloud.complete(user_token, body.session_uri)
            if binding["configuration"].get("mode") == "gateway":
                return self.finish(request, actor, flow["id"], "CONNECTED", expected="COMPLETING")
            result = self.cloud.token(user_token, binding["configuration"], sessionUri=body.session_uri)
            phase = "CONNECTED" if result.get("accessToken") else "NEEDS_CHECK"
            return self.finish(request, actor, flow["id"], phase, expected="COMPLETING")
        except Exception:
            return self.finish(request, actor, flow["id"], "NEEDS_CHECK", expected="COMPLETING")

    def check(self, request, actor, flow_id):
        user_token = self.session(request, actor)
        def reserve(db):
            flow, binding = self.owned(db, actor, flow_id)
            if flow["phase"] == "CONNECTED":
                return flow, binding, False
            if flow["expires"] <= time.time():
                raise HTTPException(410, "Authorization expired; start a new attempt")
            if not flow.get("session_uri") or flow["phase"] not in ("CONSENT_REQUIRED", "NEEDS_CHECK"):
                raise HTTPException(409, "No native session can be reconciled; start a new authorization attempt")
            flow["phase"] = "CHECKING"
            put(db, FLOW + flow_id, flow)
            return flow, binding, True
        flow, binding, dispatch = self.service.tx(reserve)
        if not dispatch:
            return self.public(flow)
        if binding["configuration"].get("mode") == "gateway":
            return self.finish(request, actor, flow["id"], "NEEDS_CHECK", expected="CHECKING")
        try:
            self.validate_native(binding)
            result = self.cloud.token(user_token, binding["configuration"], sessionUri=flow["session_uri"])
            phase = ("CONNECTED" if result.get("accessToken") else
                     "NEEDS_CHECK" if flow.get("completion_attempted") else "CONSENT_REQUIRED")
            return self.finish(request, actor, flow_id, phase, expected="CHECKING")
        except Exception:
            return self.finish(request, actor, flow_id, "NEEDS_CHECK", expected="CHECKING")


def router(service, who, journey=None):
    routes = APIRouter(prefix="/api/mcp/user-connections")
    users = UserConnections(service)

    @routes.get("")
    async def list_connections(request: Request):
        return await run_in_threadpool(users.list, request, who(request))

    @routes.get("/requests/{token}")
    async def request_status(token: str, request: Request):
        return await run_in_threadpool(users.read, request, who(request), token=token)

    @routes.get("/flows/{flow_id}")
    async def flow_status(flow_id: str, request: Request):
        return await run_in_threadpool(users.read, request, who(request), flow_id=flow_id)

    @routes.post("/flows/{flow_id}/check")
    async def check(flow_id: str, request: Request):
        return await run_in_threadpool(users.check, request, who(request), flow_id)

    @routes.post("/complete")
    async def complete(request: Request):
        actor = who(request)
        result = await run_in_threadpool(users.complete, request, actor, await request.json())
        if result["phase"] == "CONNECTED" and journey is not None:
            from .hosted_auth import SESSION_COOKIE, sha
            session_hash = sha(request.cookies.get(SESSION_COOKIE, "")) if journey.hosted else None
            continuation = await run_in_threadpool(
                journey.resume_connected_flow, actor, result["id"], session_hash=session_hash)
            if continuation:
                request.app.state.wake.set()
        return result

    @routes.post("/{server_id}/authorize")
    async def authorize(server_id: str, request: Request):
        return await run_in_threadpool(users.start, request, who(request), server_id, await request.json())

    return routes
