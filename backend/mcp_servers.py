"""Administrator-created native MCP servers with durable, non-replayed writes."""
import copy
import json
import re
import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from jsonschema import Draft202012Validator
from pydantic import Field, field_validator

from foundation_harness.config import digest
from .foundation_runs import get, put
from .journey_schema import Strict
from .live_catalog import grant_scope

PHASES = {"create": "CREATING", "grant": "GRANTING", "connect": "CONNECTING"}
TERMINAL = {"READY", "FAILED", "NEEDS_RECONCILIATION"}
IDENT = r"[A-Z][A-Z0-9_]{0,127}"
TOOL_TYPES = {"SYSTEM_EXECUTE_SQL", "CORTEX_ANALYST_MESSAGE", "CORTEX_SEARCH_SERVICE_QUERY", "CORTEX_AGENT_RUN", "GENERIC"}


class CreateMcpServer(Strict):
    profile_id: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=500)
    tools: list[str] = Field(min_length=1, max_length=6)
    workspaces: list[str] = Field(min_length=1, max_length=2)
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("name")
    @classmethod
    def name_content(cls, value):
        if len(value.strip()) < 2:
            raise ValueError("Enter a server name")
        return value.strip()


def validate_profile(profile, settings):
    """Profiles are operator-owned; reject unbound credentials or executable input."""
    p = copy.deepcopy(profile)
    if not re.fullmatch(r"[a-z0-9-]{1,60}", p["id"]):
        raise ValueError("Invalid MCP profile identifier")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.snowflakecomputing\.com", p["host"]):
        raise ValueError("Use an exact Snowflake account host")
    for field in ("database", "schema", "warehouse", "creator_role", "reader_role"):
        if not re.fullmatch(IDENT, p[field]):
            raise ValueError("Invalid configured Snowflake identifier")
    prefix = f"arn:aws:secretsmanager:{settings['region']}:{settings['account']}:secret:governed-agent-builder-serverless/"
    if not p["provisioning_secret_arn"].startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9/_+=.@-]+", p["provisioning_secret_arn"][len(prefix):]):
        raise ValueError("MCP provisioning secret is outside this deployment")
    provider_prefix = f"arn:aws:bedrock-agentcore:{settings['region']}:{settings['account']}:token-vault/default/apikeycredentialprovider/"
    if not p["credential_provider_arn"].startswith(provider_prefix):
        raise ValueError("MCP credential provider is outside this account")
    if not p["workspaces"] or not set(p["workspaces"]) <= {"research", "operations"}:
        raise ValueError("Invalid MCP profile workspaces")
    names = [t["name"] for t in p["tools"]]
    if not names or len(names) > 6 or len(set(names)) != len(names):
        raise ValueError("Invalid MCP profile tools")
    for tool in p["tools"]:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,29}", tool["name"]) or tool["type"] not in TOOL_TYPES:
            raise ValueError("Unsupported MCP capability")
        if "$$" in json.dumps(tool):
            raise ValueError("Invalid MCP specification delimiter")
        if not set(tool.get("requires", [])) <= set(names):
            raise ValueError("Missing MCP capability dependency")
        if tool["type"] == "SYSTEM_EXECUTE_SQL":
            config = tool["config"]
            if config.get("read_only") is not True or config.get("warehouse") != p["warehouse"] or not 1 <= config.get("query_timeout", 0) <= 30:
                raise ValueError("General SQL must use the configured read-only warehouse")
        else:
            if not re.fullmatch(IDENT + r"\." + IDENT + r"\." + IDENT, tool["identifier"]):
                raise ValueError("Use a fully qualified approved Snowflake resource")
        if tool["type"] == "GENERIC" and tool.get("config", {}).get("type") != "function":
            raise ValueError("Only approved custom functions are supported")
    return p


def specification(state, profile):
    selected = set(state["tool_ids"])
    return {"tools": [{k: copy.deepcopy(v) for k, v in t.items() if k != "requires"}
                      for t in profile["tools"] if t["name"] in selected]}


class McpServers:
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

    def profiles(self, db):
        config = get(db, "mcp-platform") or {}
        if not config.get("enabled"):
            return []
        return [validate_profile(p, self.settings) for p in config["profiles"]]

    def profile(self, db, profile_id):
        return next((p for p in self.profiles(db) if p["id"] == profile_id), None)

    @staticmethod
    def admin(actor):
        if actor["role"] != "admin":
            raise HTTPException(403, "Platform admin required")

    @staticmethod
    def load(db, server_id):
        state = get(db, "mcp-server:" + server_id)
        if not state:
            raise HTTPException(404, "MCP server not found")
        return state

    @staticmethod
    def public(state):
        fields = ("id", "job_id", "name", "description", "profile_id", "phase", "server_name",
                  "endpoint", "gateway_target_id", "catalog_id", "tool_ids", "workspaces", "created", "updated", "error")
        return {k: state[k] for k in fields if k in state}

    def options(self, actor):
        self.admin(actor)
        profiles = self.tx(self.profiles)
        return {"enabled": bool(profiles), "workspaces": sorted({w for p in profiles for w in p["workspaces"]}),
                "profiles": [{**{k: p[k] for k in ("id", "name", "database", "schema", "warehouse", "reader_role")},
                              "tool_choices": [{"id": t["name"], "name": t.get("title", t["name"]),
                                                **{k: t[k] for k in ("type", "description", "identifier", "requires") if k in t}}
                                               for t in p["tools"]]} for p in profiles]}

    def list(self, actor):
        self.admin(actor)
        return self.tx(lambda db: {"items": sorted(
            [self.public(s) for r in db.select("settings") if r["key"].startswith("mcp-server:")
             if not (s := json.loads(r["body"])).get("management_adopted")],
            key=lambda x: x["created"], reverse=True)})

    def detail(self, actor, server_id):
        self.admin(actor)
        return self.tx(lambda db: self.public(self.load(db, server_id)))

    def request(self, actor, token):
        self.admin(actor)
        def read(db):
            receipt = get(db, "mcp-request:" + digest([actor["id"], token]))
            if not receipt:
                raise HTTPException(404, "MCP request not found")
            return self.public(self.load(db, receipt["id"]))
        return self.tx(read)

    def create(self, actor, body, session_hash=None):
        self.admin(actor)
        payload = body.model_dump(exclude={"idempotency_key"})
        signature = digest(payload)
        def enqueue(db):
            request_key = "mcp-request:" + digest([actor["id"], body.idempotency_key])
            prior = get(db, request_key)
            if prior:
                if prior["digest"] != signature:
                    raise HTTPException(409, "This request key belongs to a different server definition")
                return prior["response"]
            profile = self.profile(db, body.profile_id)
            if profile is None:
                raise HTTPException(422, "Select a configured Snowflake profile")
            allowed = {t["name"]: t for t in profile["tools"]}
            if (len(set(body.tools)) != len(body.tools) or not set(body.tools) <= set(allowed)
                    or any(not set(allowed[t].get("requires", [])) <= set(body.tools) for t in body.tools)):
                raise HTTPException(422, "Select approved capabilities and their required SQL tool")
            if len(set(body.workspaces)) != len(body.workspaces) or not set(body.workspaces) <= set(profile["workspaces"]):
                raise HTTPException(422, "Select workspaces approved for this profile")
            existing = [json.loads(r["body"]) for r in db.select("settings") if r["key"].startswith("mcp-server:")]
            if len(existing) >= 20:
                raise HTTPException(429, "MCP server creation limit reached")
            if any(x["name"].casefold() == body.name.casefold() for x in existing):
                raise HTTPException(409, "An MCP server already uses this name")
            sid, job_id, now = uuid4().hex, uuid4().hex, time.time()
            server_name = "STUDIO_" + sid[:20].upper()
            state = {"id": sid, "job_id": job_id, **payload, "tool_ids": sorted(body.tools),
                     "owner": actor["id"], "requester": actor["id"], "phase": "QUEUED",
                     "profile_digest": digest(profile), "server_name": server_name,
                     "target_name": "studio-mcp-" + sid[:12], "catalog_id": "mcp-studio-" + sid,
                     "endpoint": f"https://{profile['host']}/api/v2/databases/{profile['database']}/schemas/{profile['schema']}/mcp-servers/{server_name}",
                     "created": now, "updated": now, "deadline": now + 900, "operations": {}, "claim": None}
            put(db, "mcp-server:" + sid, state)
            put(db, "mcp-job:" + job_id, {"server_id": sid})
            db.insert("jobs", {"id": job_id, "agent": "mcp:" + sid, "version": 1, "requester": actor["id"],
                              "idem": body.idempotency_key, "stage": "QUEUED", "result": "{}", "created": now,
                              "updated": now, "deadline": now + 900, "attempts": 0})
            if session_hash:
                db.insert("job_authority", {"id": job_id, "session_hash": session_hash})
            response = {"id": sid, "job_id": job_id, "phase": "QUEUED"}
            put(db, request_key, {"id": sid, "digest": signature, "response": response})
            self.audit(db, actor["id"], "mcp_creation_requested", sid, payload)
            return response
        return self.tx(enqueue)

    @staticmethod
    def audit(db, actor, action, sid, detail):
        db.insert("audit", {"actor": actor, "action": action, "resource": sid,
                           "detail": json.dumps(detail), "created": time.time()})

    def authority(self, db, state):
        profile = self.profile(db, state["profile_id"])
        if not profile or digest(profile) != state["profile_digest"]:
            raise ValueError("MCP_PROFILE_CHANGED")
        if self.hosted:
            authority = db.select("job_authority", where=[("id", "=", state["job_id"])]).fetchone()
            session = db.select("hosted_sessions", where=[("id_hash", "=", authority["session_hash"])]).fetchone() if authority else None
            if not session or session["expires"] <= time.time() or session["subject"] != state["requester"]:
                raise ValueError("MCP_AUTHORIZATION_EXPIRED")
            claims = self.auth.verify(session["access_token"], "access")
            policy = self.auth.membership(claims, dict(session).get("active_group"))
            if claims["sub"] != state["requester"] or policy["role"] != "admin":
                raise ValueError("MCP_ADMIN_MEMBERSHIP_REQUIRED")
        return profile

    def persist(self, db, state):
        state["updated"] = time.time()
        put(db, "mcp-server:" + state["id"], state)
        stage = {"READY": "SUCCEEDED", "FAILED": "FAILED", "NEEDS_RECONCILIATION": "UNKNOWN"}.get(state["phase"], state["phase"])
        db.update("jobs", {"stage": stage, "updated": state["updated"], "result": json.dumps(self.public(state))},
                  where=[("id", "=", state["job_id"])])

    def reconcile(self, actor, server_id, session_hash=None):
        self.admin(actor)
        def enqueue(db):
            state = self.load(db, server_id)
            if state["phase"] != "NEEDS_RECONCILIATION":
                raise HTTPException(409, "This server does not need reconciliation")
            if state.get("claim") and state["claim"]["expires"] > time.time():
                raise HTTPException(409, "The current operation is still running")
            previous_job = state["job_id"]
            job_id, now = uuid4().hex, time.time()
            state.setdefault("previous_jobs", []).append(previous_job)
            state.update(phase=PHASES.get(state["pending_stage"], "VERIFYING"), error=None, claim=None,
                         requester=actor["id"], deadline=now + 900, job_id=job_id)
            # Hosted dispatch consumes jobs INSERT events. Retain the terminal
            # attempt and insert a continuation, preserving every native intent.
            put(db, "mcp-job:" + job_id, {"server_id": server_id})
            db.insert("jobs", {"id": job_id, "agent": "mcp:" + server_id, "version": 1, "requester": actor["id"],
                              "idem": job_id, "stage": state["phase"], "result": "{}", "created": now,
                              "updated": now, "deadline": now + 900, "attempts": 0})
            if session_hash:
                db.insert("job_authority", {"id": job_id, "session_hash": session_hash})
            self.persist(db, state)
            self.audit(db, actor["id"], "mcp_reconciliation_requested", server_id,
                       {"stage": state["pending_stage"], "previous_job_id": previous_job, "job_id": job_id})
            return {"id": server_id, "job_id": state["job_id"], "phase": state["phase"]}
        return self.tx(enqueue)

    def step(self, job_id):
        token = uuid4().hex
        def claim(db):
            link = get(db, "mcp-job:" + job_id)
            if not link:
                return None
            state = self.load(db, link["server_id"])
            if state["job_id"] != job_id:
                return None
            if state["phase"] in TERMINAL or (state.get("claim") and state["claim"]["expires"] > time.time()):
                return None
            try:
                profile = self.authority(db, state)
                if time.time() > state["deadline"]:
                    raise ValueError("MCP_DEADLINE_EXCEEDED")
            except Exception:
                state.update(phase="FAILED", claim=None, error={"code": "AUTHORITY_CHANGED",
                             "message": "Creation stopped because its configured profile, authorization or deadline changed."})
                self.persist(db, state)
                return None
            stage = next((s for s in PHASES if state["operations"].get(s, {}).get("status") != "COMPLETE"), "verify")
            state.update(phase=PHASES.get(stage, "VERIFYING"), pending_stage=stage,
                         claim={"token": token, "expires": time.time() + 360})
            self.persist(db, state)
            return state, profile, stage
        work = self.tx(claim)
        if not work:
            return
        state, profile, stage = work

        def update(operation):
            def save(db):
                current = self.load(db, state["id"])
                if (current.get("claim") or {}).get("token") != token:
                    raise RuntimeError("MCP operation lease changed")
                return operation(db, current)
            return self.tx(save)

        try:
            if stage == "verify":
                tools = self.cloud.discover(state, profile)
                if tools is None:
                    update(lambda db, current: (current.update(claim=None), self.persist(db, current)))
                    return
                def finish(db, current):
                    self.authority(db, current)
                    self.publish(db, current, profile, tools)
                    current.update(phase="READY", claim=None, error=None)
                    self.persist(db, current)
                    self.audit(db, current["requester"], "mcp_published", current["id"],
                               {"catalog_id": current["catalog_id"], "tools": current["tool_ids"], "workspaces": current["workspaces"]})
                update(finish)
                return
            operation = state["operations"].get(stage, {})
            receipt = self.cloud.read(stage, state, profile)
            if not operation.get("intent"):
                if receipt is not None:
                    raise ValueError("MCP_RESOURCE_ALREADY_EXISTS")
                def intent(db, current):
                    self.authority(db, current)
                    current["operations"][stage] = {"intent": True, "started": time.time(), "status": "DISPATCHING"}
                    self.persist(db, current)
                update(intent)
                state["operations"][stage] = {"intent": True}
                self.cloud.write(stage, state, profile)
                receipt = self.cloud.read(stage, state, profile)
            if receipt is None:
                raise TimeoutError("Native write is not yet reconciled")
            def complete(db, current):
                current["operations"][stage].update(status="COMPLETE", receipt=receipt)
                if stage == "connect":
                    current["gateway_target_id"] = receipt["target_id"]
                current.update(phase={"create": "GRANTING", "grant": "CONNECTING", "connect": "VERIFYING"}[stage], claim=None)
                self.persist(db, current)
            update(complete)
        except Exception as exc:
            def failed(db, current):
                intended = any(op.get("intent") for op in current["operations"].values())
                current.update(phase="NEEDS_RECONCILIATION" if intended else "FAILED", claim=None,
                               error={"code": type(exc).__name__,
                                      "message": "The native operation needs a status check; its write will not be repeated."
                                      if intended else "Creation stopped before publication. Check the configured connection and selected resources."})
                self.persist(db, current)
            update(failed)

    def publish(self, db, state, profile, descriptors):
        prefix = state["target_name"] + "___"
        if len(descriptors) != len(state["tool_ids"]) or {t["name"] for t in descriptors} != {prefix + n for n in state["tool_ids"]}:
            raise ValueError("MCP discovery does not match selected capabilities")
        base = {"catalog": "journey", "version": "1", "approved": True, "fixture": False, "external": True,
                "origin": "AI Catalog", "provider": "Snowflake", "protocol": "MCP", "execution_ready": True,
                "integration_ready": True, "supported": True, "requestable": True, "managed_by": "studio-mcp",
                "discoverable_workspaces": state["workspaces"], "default_grant_workspaces": state["workspaces"],
                "approved_external_workspaces": state["workspaces"], "mcp_creation_id": state["id"],
                "data_handling": "Selected tool arguments are sent to the configured Snowflake-managed MCP server."}
        binding = {"type": "mcp-server", "gateway_id": self.settings["gateway_id"], "target_id": state["gateway_target_id"]}
        server = {**base, "id": state["catalog_id"], "name": state["name"], "kind": "mcp_server",
                  "description": state["description"] or "Snowflake-managed data access through AgentCore Gateway.",
                  "binding": binding, "binding_digest": digest(binding), "default_tool_ids": []}
        items, configured = [server], {t["name"]: t for t in profile["tools"]}
        for descriptor in sorted(descriptors, key=lambda t: t["name"]):
            name, schema = descriptor["name"], descriptor.get("inputSchema")
            if not isinstance(schema, dict) or schema.get("type") != "object":
                raise ValueError("Missing native input schema")
            Draft202012Validator.check_schema(schema)
            operation = name[len(prefix):]
            binding = {"type": "mcp", "gateway_id": self.settings["gateway_id"], "target_id": state["gateway_target_id"],
                       "name": name, "inputSchema": schema, "schema_digest": digest(schema)}
            if configured[operation]["type"] == "CORTEX_AGENT_RUN":
                binding["response_adapter"] = "snowflake-cortex-agent"
            cid = "mcp-tool-" + state["id"][:20] + "-" + operation
            items.append({**base, "id": cid, "name": descriptor.get("title") or configured[operation].get("title", operation),
                          "kind": "tool", "description": descriptor.get("description", ""), "inputSchema": schema,
                          "operation": operation, "parent_id": server["id"], "binding": binding, "binding_digest": digest(binding)})
            if operation == "query_sql" or "query_sql" not in state["tool_ids"]:
                server["default_tool_ids"].append(cid)
        for item in items:
            if db.select("components", where=[("id", "=", item["id"])]).fetchone():
                raise ValueError("Catalog identity already exists")
            db.insert("components", {"id": item["id"], "body": json.dumps(item)})
            for row in db.select("principals"):
                actor = json.loads(row["body"])
                if actor["role"] == "business" and actor["workspace"] in state["workspaces"]:
                    db.insert("grants", {"persona": actor["id"], "component": item["id"]}, ignore=True)
                    put(db, grant_scope(actor, item["id"]), True)


def router(service, who):
    routes = APIRouter(prefix="/api/admin/mcp")

    def session_hash(request):
        if not service.hosted:
            return None
        from .hosted_auth import SESSION_COOKIE, sha
        return sha(request.cookies.get(SESSION_COOKIE, ""))

    @routes.get("/options")
    def options(request: Request):
        return service.options(who(request, True))

    @routes.get("/servers")
    def servers(request: Request):
        return service.list(who(request, True))

    @routes.get("/requests/{token}")
    def request_receipt(token: str, request: Request):
        return service.request(who(request, True), token)

    @routes.post("/servers", status_code=202)
    def create(body: CreateMcpServer, request: Request):
        result = service.create(who(request, True), body, session_hash(request))
        request.app.state.wake.set()
        return result

    @routes.get("/servers/{server_id}")
    def detail(server_id: str, request: Request):
        return service.detail(who(request, True), server_id)

    @routes.post("/servers/{server_id}/reconcile", status_code=202)
    def reconcile(server_id: str, request: Request):
        result = service.reconcile(who(request, True), server_id, session_hash(request))
        request.app.state.wake.set()
        return result

    return routes
