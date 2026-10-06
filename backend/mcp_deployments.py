"""Confirmed retirement of owned MCP deployments, with durable read reconciliation."""
import copy
import json
import re
import time
from urllib.parse import quote

from fastapi import HTTPException

from foundation_harness.config import digest
from .foundation_runs import get, put
from .mcp_management import dependencies


def states(db):
    packages, runtimes = {}, {}
    for row in db.select("settings"):
        if row["key"].startswith("mcp-package:"):
            value = json.loads(row["body"])
            packages[value["id"]] = value
        elif row["key"].startswith("mcp-python:"):
            value = json.loads(row["body"])
            runtimes[value["id"]] = value
    return {**packages, **runtimes}


def active_ids(db):
    return {sid for sid, state in states(db).items() if state.get("phase") != "DELETED"}


def endpoints(state):
    result = {state[k] for k in ("endpoint", "bearer_endpoint") if state.get(k)}
    arn = state.get("runtime_arn", "")
    match = re.fullmatch(r"arn:aws:bedrock-agentcore:([a-z0-9-]+):\d{12}:runtime/[A-Za-z0-9_-]+", arn)
    if match:
        result.add(f"https://bedrock-agentcore.{match[1]}.amazonaws.com/runtimes/"
                   + quote(arn, safe="") + "/invocations?qualifier=DEFAULT")
    return result


def assert_endpoint_available(db, endpoint):
    if get(db, "mcp-retired-endpoint:" + digest(endpoint)):
        raise HTTPException(409, "This uploaded MCP is being deleted or has been deleted")


def blockers(db, state):
    found = []
    ids = [state["id"], *endpoints(state)]
    ids.extend(state[k] for k in ("runtime_id", "runtime_arn") if state.get(k))
    for row in db.select("settings"):
        if row["key"].startswith(("mcp-connection:", "mcp-server:")):
            connection = json.loads(row["body"])
            if connection.get("phase") != "DELETED" and connection.get("endpoint") in endpoints(state):
                found.append({"id": connection["id"], "name": connection["name"], "kind": "connection"})
                if connection.get("catalog_id"):
                    ids.append(connection["catalog_id"])
    for row in db.select("components"):
        component = json.loads(row["body"])
        binding = component.get("binding", {})
        if binding.get("endpoint") in endpoints(state):
            ids.append(component["id"])
    return [*found, *[{**agent, "kind": "agent"} for agent in dependencies(db, ids)]]


class Deployments:
    def __init__(self, python):
        self.python, self.service = python, python.service

    @staticmethod
    def load(db, sid):
        state = get(db, "mcp-python:" + sid) or get(db, "mcp-package:" + sid)
        if not state:
            raise HTTPException(404, "MCP deployment not found")
        return state

    def info(self, db, state):
        pending = "stage" not in state
        value = ({**{k: state[k] for k in ("id", "name", "filename", "created")},
                  "phase": state.get("phase", "UPLOADING"), "upload_type": "package"}
                 if pending else self.python.public(state))
        used = blockers(db, state)
        available = (value["phase"] in ("UPLOADING", "READY", "FAILED", "NEEDS_RECONCILIATION")
                     and not state.get("deletion")
                     and (state.get("claim") or {}).get("expires", 0) <= time.time()
                     and not any(p.get("claim_expires", 0) > time.time() for p in state.get("parts", {}).values()))
        return {**value, "revision": state.get("revision", 1), "blockers": used,
                "can_delete": available and not used,
                "reason": ("Remove its unused MCP registrations first; saved-agent references block deletion."
                           if used else "" if available or value["phase"] == "DELETED"
                           else "Finish or reconcile the current deployment operation first.")}

    def list(self, actor):
        self.service.admin(actor)
        return self.service.tx(lambda db: {"items": sorted(
            [self.info(db, value) for value in states(db).values() if value.get("phase") != "DELETED"],
            key=lambda item: item["created"], reverse=True)})

    def detail(self, actor, sid):
        self.service.admin(actor)
        return self.service.tx(lambda db: self.info(db, self.load(db, sid)))

    def delete(self, actor, sid, body, session_hash):
        self.service.admin(actor)
        def reserve(db):
            key = "mcp-deployment-delete:" + digest([actor["id"], sid, body.idempotency_key])
            prior = get(db, key)
            fingerprint = digest(body.model_dump())
            if prior:
                if prior["digest"] != fingerprint:
                    raise HTTPException(409, "This deletion request has different contents")
                return self.python.public(self.python.load(db, sid))
            state, config = self.load(db, sid), self.python.config(db)
            info = self.info(db, state)
            if body.expected_revision != info["revision"] or not info["can_delete"]:
                raise HTTPException(409, info["reason"] or "Deployment changed; refresh its current status")
            if body.confirm_name != state["name"]:
                raise HTTPException(422, "Enter the exact MCP deployment name to confirm deletion")
            if "stage" not in state:
                from .mcp_package import PackageUploads
                binding = PackageUploads(self.python).config(db)
                if digest(binding) != state["config_digest"]:
                    raise HTTPException(409, "The original package deployment binding changed")
                state.update(upload_type="package", runtime_name=config["runtime_prefix"] + "_" + sid[:24],
                             config_digest=digest(config), operations={}, endpoint="")
            elif digest(config) != state["config_digest"]:
                raise HTTPException(409, "The original deployment binding changed")
            state.update(deployment_operations=copy.deepcopy(state["operations"]),
                         operations={}, phase="DELETING", stage="delete_inventory", retry_count=0,
                         deletion={"requested_at": time.time(), "resources": None})
            state.pop("failure_code", None)
            for endpoint in endpoints(state):
                put(db, "mcp-retired-endpoint:" + digest(endpoint), {"id": sid})
            response = self.python.job(db, state, actor, session_hash)
            package = get(db, "mcp-package:" + sid)
            if package:
                package.update(job_id=response["job_id"], phase="DELETING")
                put(db, "mcp-package:" + sid, package)
            put(db, key, {"digest": fingerprint, "id": sid})
            self.service.audit(db, actor["id"], "mcp_deployment_delete_requested", sid,
                               {"revision": info["revision"]})
            return response
        return self.service.tx(reserve)


def advance(python, state, config, update):
    """One resource per durable worker step. Unknown writes are never replayed."""
    def complete(current):
        current.update(phase="DELETED", stage="deleted", deleted_at=time.time(),
                       revision=current.get("revision", 1) + 1, failure_code=None)

    if state["stage"] == "delete_inventory":
        resources = python.cloud.retirement_plan(state, config)
        if resources is None:
            update(lambda current: None)
            return
        def plan(current):
            current["deletion"]["resources"] = resources
            native = next((r for r in resources if r["kind"] == "runtime"), None)
            if native:
                current.update({k: native[k] for k in ("runtime_id", "runtime_arn", "runtime_version") if k in native})
            if resources:
                current["stage"] = "retire_0"
            else:
                complete(current)
        update(plan)
        return
    stage = state["stage"]
    index = int(stage.removeprefix("retire_"))
    resources = state["deletion"]["resources"]
    resource = resources[index]
    operation = state["operations"].get(stage, {})
    receipt = python.cloud.retirement_read(resource, state, config)
    if not receipt["absent"] and not receipt["pending"]:
        if operation and not operation.get("retry_requested"):
            raise ValueError("Deletion needs an explicit retry after reading its unchanged resource")
        update(lambda current: current["operations"].update({
            stage: {"status": "INTENT", "retry_requested": False}}), release=False)
        python.cloud.retirement_write(resource, state, config)
        update(lambda current: current["operations"][stage].update(status="ACKNOWLEDGED"), release=False)
        receipt = python.cloud.retirement_read(resource, state, config)
    if not receipt["absent"]:
        update(lambda current: None)
        return
    def finish(current):
        current["operations"][stage] = {"status": "COMPLETE", "absent": True}
        current["failure_code"] = None
        if index + 1 == len(resources):
            complete(current)
        else:
            current["stage"] = "retire_" + str(index + 1)
    update(finish)
