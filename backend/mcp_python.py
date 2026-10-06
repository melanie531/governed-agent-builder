"""Administrator Python uploads, packaged and deployed by the existing job worker."""
import ast
import hashlib
import json
import time
from uuid import uuid4

from fastapi import HTTPException
from pydantic import Field

from foundation_harness.config import digest
from .foundation_runs import get, put
from .journey_schema import Strict
from .mcp_credentials import failure_code

STAGES = ("package", "runtime", "logs", "schema")
PHASES = {"package": "PACKAGING", "runtime": "DEPLOYING", "logs": "DEPLOYING", "schema": "DISCOVERING"}
STOPPED = {"READY", "FAILED", "NEEDS_RECONCILIATION", "DELETED"}


class PythonInput(Strict):
    name: str = Field(min_length=2, max_length=80)
    filename: str = Field(pattern=r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}\.py$")
    source: str = Field(min_length=1, max_length=65536)
    snowflake_account: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    snowflake_role: str = Field(pattern=r"^[A-Z_][A-Z0-9_]{0,254}$")
    warehouse: str = Field(pattern=r"^[A-Z_][A-Z0-9_]{0,254}$")
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class PythonMcp:
    def __init__(self, service, cloud=None):
        from .mcp_python_cloud import PythonCloud
        self.service = service
        self.cloud = cloud or PythonCloud(service.settings)

    def config(self, db):
        from .mcp_python_cloud import configuration
        return configuration(get(db, "mcp-python-config"), self.service.settings)

    @staticmethod
    def load(db, sid):
        state = get(db, "mcp-python:" + sid)
        if not state:
            raise HTTPException(404, "Python MCP not found")
        return state

    @staticmethod
    def public(state):
        return {**{k: state[k] for k in ("id", "job_id", "name", "filename", "source_digest", "phase", "stage",
            "endpoint", "tools", "runtime_id", "runtime_arn", "runtime_version", "created", "failure_code",
            "upload_type", "connection_mode", "size", "bearer_endpoint", "revision", "deleted_at") if k in state},
            "deleting": bool(state.get("deletion")),
            "retry_available": (state["phase"] == "NEEDS_RECONCILIATION" and state["stage"] != "schema"
                and bool(state["operations"].get(state["stage"])) and state.get("retry_count", 0) < 3)}

    def persist(self, db, state):
        previous = get(db, "mcp-python:" + state["id"]) if state["phase"] == "DELETED" else None
        put(db, "mcp-python:" + state["id"], state)
        if state.get("deletion"):
            from .mcp_deployments import endpoints
            for endpoint in endpoints(state):
                put(db, "mcp-retired-endpoint:" + digest(endpoint), {"id": state["id"]})
        if state["phase"] == "DELETED":
            db.delete("settings", where=[("key", "=", "mcp-python-source:" + state["id"])])
            package = get(db, "mcp-package:" + state["id"])
            if package:
                package.update(phase="DELETED", deleted_at=state["deleted_at"])
                put(db, "mcp-package:" + state["id"], package)
            if not previous or previous["phase"] != "DELETED":
                self.service.audit(db, state["requester"], "mcp_deployment_deleted", state["id"],
                                   {"revision": state["revision"], "resources": len(state["deletion"]["resources"])})
        stage = {"READY": "SUCCEEDED", "DELETED": "SUCCEEDED", "FAILED": "FAILED",
                 "NEEDS_RECONCILIATION": "UNKNOWN"}.get(state["phase"], state["phase"])
        db.update("jobs", {"stage": stage, "updated": time.time(), "result": json.dumps(self.public(state))},
                  where=[("id", "=", state["job_id"])])

    def job(self, db, state, actor, session_hash):
        job_id, now = uuid4().hex, time.time()
        state.update(job_id=job_id, requester=actor["id"], deadline=now + 900, claim=None)
        put(db, "mcp-python-job:" + job_id, {"server_id": state["id"]})
        db.insert("jobs", {"id": job_id, "agent": "mcp-python:" + state["id"], "version": 1,
            "requester": actor["id"], "idem": job_id, "stage": "QUEUED", "result": "{}",
            "created": now, "updated": now, "deadline": now + 900, "attempts": 0})
        if session_hash:
            db.insert("job_authority", {"id": job_id, "session_hash": session_hash})
        self.persist(db, state)
        return self.public(state)

    def create(self, actor, value, session_hash=None):
        self.service.admin(actor)
        try:
            body = PythonInput.model_validate(value)
            if (len(body.name.strip()) < 2 or len(body.source.encode()) > 65536
                    or body.snowflake_role in {"ACCOUNTADMIN", "SECURITYADMIN", "USERADMIN", "SYSADMIN", "ORGADMIN"}):
                raise ValueError()
            ast.parse(body.source, filename="main.py")
        except Exception:
            raise HTTPException(422, "Upload a valid UTF-8 .py file of at most 64 KiB and use a dedicated Snowflake reader role and warehouse") from None
        metadata = {**body.model_dump(exclude={"source", "idempotency_key"}),
                    "source_digest": hashlib.sha256(body.source.encode()).hexdigest()}
        def reserve(db):
            config = self.config(db)
            if "artifact" not in config:
                raise HTTPException(409, "An installed bundle is required for single-file uploads; upload a complete ZIP instead")
            key = "mcp-python-request:" + digest([actor["id"], body.idempotency_key])
            prior = get(db, key)
            if prior:
                state = self.load(db, prior["id"])
                if state["request_digest"] != digest(metadata):
                    raise HTTPException(409, "The retained Python upload has different source or settings")
                return self.public(state)
            from .mcp_deployments import active_ids
            if len(active_ids(db)) >= 20:
                raise HTTPException(429, "Python MCP runtime limit reached")
            sid = uuid4().hex
            state = {**metadata, "id": sid, "name": body.name.strip(), "phase": "PACKAGING", "stage": "package",
                "created": time.time(), "operations": {}, "config_digest": digest(config), "request_digest": digest(metadata),
                "runtime_name": config["runtime_prefix"] + "_" + sid[:24], "endpoint": config["facade_url"] + "/mcp/" + sid}
            # Private encrypted application storage; neither public status nor audit contains source.
            put(db, "mcp-python-source:" + sid, body.source)
            put(db, key, {"id": sid})
            self.service.audit(db, actor["id"], "mcp_python_upload_requested", sid, {
                "source_digest": metadata["source_digest"], "filename": body.filename})
            return self.job(db, state, actor, session_hash)
        try:
            return self.service.tx(reserve)
        except ValueError:
            raise HTTPException(409, "Python MCP deployment is not configured") from None

    def list(self, actor):
        self.service.admin(actor)
        return self.service.tx(lambda db: {"items": sorted(
            [self.public(state) for r in db.select("settings") if r["key"].startswith("mcp-python:")
             if (state := json.loads(r["body"]))["phase"] != "DELETED"],
            key=lambda x: x["created"], reverse=True)})

    def detail(self, actor, sid):
        self.service.admin(actor)
        return self.service.tx(lambda db: self.public(self.load(db, sid)))

    def request(self, actor, token):
        self.service.admin(actor)
        def read(db):
            prior = get(db, "mcp-python-request:" + digest([actor["id"], token]))
            if not prior:
                raise HTTPException(404, "Python upload request not found")
            return self.public(self.load(db, prior["id"]))
        return self.service.tx(read)

    def authority(self, db, state):
        config = self.config(db)
        if digest(config) != state["config_digest"] or state["deadline"] < time.time():
            raise ValueError("Python deployment configuration or deadline changed")
        self.service.authorize_job(db, state)
        if state.get("deletion"):
            from .mcp_deployments import blockers
            if blockers(db, state):
                raise ValueError("MCP deployment dependencies changed")
        return config

    def resume(self, actor, sid, session_hash=None, retry_job_id=None):
        self.service.admin(actor)
        def enqueue(db):
            state = self.load(db, sid)
            if (state["phase"] not in ("FAILED", "NEEDS_RECONCILIATION")
                    or (state.get("claim") or {}).get("expires", 0) > time.time()
                    or digest(self.config(db)) != state["config_digest"]):
                raise HTTPException(409, "Refresh Python MCP status; its operation or configuration changed")
            if retry_job_id:
                if retry_job_id != state["job_id"] or not self.public(state)["retry_available"]:
                    raise HTTPException(409, "Retry must match the current retained Python deployment")
                state["retry_count"] = state.get("retry_count", 0) + 1
                state["operations"][state["stage"]]["retry_requested"] = True
            state["phase"] = "DELETING" if state.get("deletion") else PHASES[state["stage"]]
            return self.job(db, state, actor, session_hash)
        return self.service.tx(enqueue)

    def step(self, job_id):
        token = uuid4().hex
        def claim(db):
            link = get(db, "mcp-python-job:" + job_id)
            if not link:
                return None
            state = self.load(db, link["server_id"])
            if (state["job_id"] != job_id or state["phase"] in STOPPED
                    or (state.get("claim") or {}).get("expires", 0) > time.time()):
                return None
            try:
                config = self.authority(db, state)
            except Exception:
                state.update(phase="FAILED", failure_code="AUTHORITY_CHANGED", claim=None)
                self.persist(db, state)
                return None
            state["claim"] = {"token": token, "expires": time.time() + 360}
            self.persist(db, state)
            return state, config
        work = self.service.tx(claim)
        if not work:
            return
        state, config = work
        stage = state["stage"]
        def update(action, release=True):
            def save(db):
                current = self.load(db, state["id"])
                if current["job_id"] != job_id or (current.get("claim") or {}).get("token") != token:
                    raise ValueError("Python deployment claim changed")
                self.authority(db, current)
                action(current)
                if release:
                    current["claim"] = None
                self.persist(db, current)
            self.service.tx(save)
        try:
            if state.get("deletion"):
                from .mcp_deployments import advance
                advance(self, state, config, update)
                return
            if stage == "schema":
                from .mcp_onboarding import discovered_tools
                discovered = self.cloud.discover(state, config)
                validator = state.get("bearer_validation_tool")
                if validator:
                    candidates = [t for t in discovered if t.get("name") == validator]
                    if (len(candidates) != 1 or candidates[0].get("inputSchema", {}).get("type") != "object"
                            or candidates[0]["inputSchema"].get("properties")
                            or candidates[0]["inputSchema"].get("required")
                            or candidates[0].get("annotations", {}).get("readOnlyHint") is not True):
                        raise ValueError("Package bearer validator must be a read-only tool with no arguments")
                    discovered = [t for t in discovered if t.get("name") != validator]
                tools = discovered_tools({}, discovered)
                update(lambda current: current.update(tools=tools, phase="READY", failure_code=None))
                return
            operation = state["operations"].get(stage, {})
            receipt = self.cloud.read(stage, state, config)
            if not operation or (receipt is None and operation.get("retry_requested")):
                if not operation and receipt is not None:
                    raise ValueError("Unexpected preexisting Python resource")
                update(lambda current: current["operations"].update({
                    stage: {"status": "INTENT", "retry_requested": False}}), release=False)
                source = self.service.tx(lambda db: get(db, "mcp-python-source:" + state["id"])) if stage == "package" else None
                self.cloud.write(stage, state, config, source)
                update(lambda current: current["operations"][stage].update(status="ACKNOWLEDGED"), release=False)
                operation = {"status": "ACKNOWLEDGED"}
                receipt = self.cloud.read(stage, state, config)
            if receipt is None:
                if operation.get("status") != "ACKNOWLEDGED":
                    raise ValueError("Retained Python operation has no confirmed result")
                update(lambda current: None)
                return
            if receipt.get("pending"):
                update(lambda current: None)
                return
            def finish(current):
                current.update(receipt, stage=STAGES[STAGES.index(stage) + 1])
                if stage == "runtime" and current.get("connection_mode") in ("IAM", "PACKAGE"):
                    from urllib.parse import quote
                    current["endpoint"] = (f"https://bedrock-agentcore.{self.service.settings['region']}.amazonaws.com/runtimes/"
                        + quote(current["runtime_arn"], safe="") + "/invocations?qualifier=DEFAULT")
                    if current.get("bearer_validation_tool"):
                        current["bearer_endpoint"] = config["facade_url"] + "/mcp/" + current["id"]
                current.update(phase=PHASES[current["stage"]], failure_code=None)
                current["operations"][stage] = {"status": "COMPLETE", "receipt": receipt}
            update(finish)
        except Exception as error:
            from .mcp_package import InvalidPackage
            from .mcp_python_cloud import McpDiscoveryError
            code = ("INVALID_PACKAGE" if isinstance(error, InvalidPackage) else
                    "MCP_DISCOVERY_FAILED" if isinstance(error, McpDiscoveryError) else failure_code(error))
            def fail(db):
                current = self.load(db, state["id"])
                if current["job_id"] == job_id and (current.get("claim") or {}).get("token") == token:
                    current.update(phase="FAILED" if isinstance(error, InvalidPackage) else "NEEDS_RECONCILIATION",
                                   failure_code=code, claim=None)
                    self.persist(db, current)
            self.service.tx(fail)
