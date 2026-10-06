"""Revision-fenced management of unused MCP registrations and their bindings."""
import copy
import json
import time
from uuid import uuid4

from fastapi import HTTPException
from pydantic import Field

from foundation_harness.config import digest
from .foundation_runs import get, put
from .journey_schema import Strict
from .live_catalog import grant_scope


class DeleteConnection(Strict):
    expected_revision: int = Field(ge=1)
    confirm_name: str = Field(min_length=2, max_length=80)
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


def legacy_public(state):
    """Read frozen registrations without enabling their retired provisioning path."""
    fields = ("id", "job_id", "name", "description", "phase", "endpoint", "gateway_target_id",
              "catalog_id", "tool_ids", "workspaces", "created", "updated", "error")
    result = {k: state[k] for k in fields if k in state}
    if state["phase"] not in ("READY", "FAILED", "NEEDS_RECONCILIATION"):
        result.update(phase="NEEDS_RECONCILIATION",
                      error="Remote server provisioning has been retired. Inspect the recorded remote operation "
                            "before onboarding its existing endpoint.")
    return result


def legacy_list(service, actor):
    service.admin(actor)
    return service.tx(lambda db: {"items": sorted(
        [legacy_public(state) for row in db.select("settings")
         if row["key"].startswith("mcp-server:")
         if not (state := json.loads(row["body"])).get("management_adopted")],
        key=lambda state: state["created"], reverse=True)})


def legacy_detail(service, actor, sid):
    service.admin(actor)
    def read(db):
        state = get(db, "mcp-server:" + sid)
        if not state:
            raise HTTPException(404, "MCP server not found")
        return legacy_public(state)
    return service.tx(read)


def component_ids(db, state):
    return sorted({r["id"] for row in db.select("components") if (
        (r := json.loads(row["body"]))["id"] == state["catalog_id"]
        or r.get("parent_id") == state["catalog_id"]
        or (state.get("gateway_target_id") and r.get("binding", {}).get("target_id") == state["gateway_target_id"]))
    } | set(state.get("publication_catalog_ids", [])))


def strings(value):
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return set().union(*(strings(v) for v in value))
    if isinstance(value, dict):
        return set(value) | set().union(*(strings(v) for v in value.values()))
    return set()


def dependencies(db, ids):
    agents = {r["id"]: dict(r) for r in db.select("agents")}
    found = {}
    for row in db.select("versions"):
        if row["agent"] not in agents:
            continue
        body = json.loads(row["body"])
        if set(ids) & strings(body):
            item = found.setdefault(row["agent"], {"id": row["agent"], "name": body.get("name", row["agent"]),
                                                   "workspace": agents[row["agent"]]["workspace"], "versions": []})
            item["versions"].append(row["version"])
    return sorted(found.values(), key=lambda a: a["name"])


def load(service, db, source, sid):
    if source not in ("onboarding", "servers"):
        raise HTTPException(404, "Connection not found")
    state = get(db, ("mcp-connection:" if source == "onboarding" else "mcp-server:") + sid)
    if not state:
        raise HTTPException(404, "Connection not found")
    if source == "servers":
        if state.get("management_adopted"):
            return service.load(db, sid)
        # Only the saved reader-provider reference is needed to manage an old
        # registration. Do not validate or execute its former creation profile.
        profiles = get(db, "mcp-platform") or {}
        profile = next((p for p in profiles.get("profiles", [])
                        if p["id"] == state["profile_id"]), None) if profiles.get("enabled") else None
        if not profile:
            raise HTTPException(409, "The original connection profile is unavailable")
        config = service.config(db)
        connection = next((c for c in config["connections"] if c["configuration"] == {
            "credentialProviderType": "API_KEY", "credentialProvider": {"apiKeyCredentialProvider": {
                "providerArn": profile["credential_provider_arn"], "credentialParameterName": "Authorization",
                "credentialPrefix": "Bearer", "credentialLocation": "HEADER"}}}), None)
        if not connection:
            raise HTTPException(409, "The existing authentication connection is unavailable")
        state = {**state, "connection_id": connection["id"], "legacy_source": True, "stage": "publish"}
    return state


def available(state):
    return (state["phase"] in ("READY", "REVIEW", "FAILED", "NEEDS_RECONCILIATION")
            and not state.get("change") and not state.get("claim"))


def info(service, actor, source, sid):
    service.admin(actor)
    def read(db):
        state = load(service, db, source, sid)
        blocked = dependencies(db, component_ids(db, state) + [state["catalog_id"]])
        return {**service.public(state), "blockers": blocked, "can_edit": available(state) and not blocked,
                "can_delete": available(state) and not blocked,
                "reason": ("This connection is referenced by saved agent versions." if blocked else
                           "Finish or reconcile the current operation first." if not available(state) else ""),
                "source": source, "revision": state.get("revision", 1),
                "tool_schema": state.get("tool_schema")}
    return service.tx(read)


def begin(service, actor, source, sid, body, kind, session_hash):
    from .mcp_onboarding import binding_digest
    service.admin(actor)
    payload = body.model_dump()
    def reserve(db):
        key = "mcp-management-request:" + digest([actor["id"], source, sid, body.idempotency_key])
        previous = get(db, key)
        if previous:
            if previous["digest"] != digest([kind, payload]):
                raise HTTPException(409, "This request key belongs to a different change")
            return previous["response"]
        state = load(service, db, source, sid)
        if body.expected_revision != state.get("revision", 1) or not available(state):
            raise HTTPException(409, "Connection changed; refresh its current status")
        ids = component_ids(db, state)
        if dependencies(db, ids + [state["catalog_id"]]):
            raise HTTPException(409, "Connection is used by saved agents; inspect its dependencies before changing it")
        config = service.config(db)
        if kind == "edit":
            from .mcp_deployments import assert_endpoint_available
            assert_endpoint_available(db, body.endpoint)
            from .mcp_auth_management import unlocked
            unlocked(db, body.connection_id)
            connection = next((c for c in config["connections"] if c["id"] == body.connection_id), None)
            try:
                from .mcp_onboarding import endpoint_allowed, discovered_tools
                if (not connection or not endpoint_allowed(connection, body.endpoint)
                        or len(set(body.workspaces)) != len(body.workspaces)
                        or not set(body.workspaces) <= set(config["workspaces"])):
                    raise ValueError()
                native_oauth = connection.get("user_authorization", {}).get("mode") == "gateway"
                if bool(body.tool_schema) != native_oauth:
                    raise ValueError()
                schema = discovered_tools({"target_name": "schema"}, [
                    {**t, "name": "schema___" + t["name"]} for t in body.tool_schema]) if native_oauth else None
            except (ValueError, KeyError, TypeError):
                raise HTTPException(422, "Choose a permitted endpoint, authentication connection and workspaces; user OAuth requires a valid tool schema") from None
            for row in db.select("settings"):
                if row["key"].startswith("mcp-connection:"):
                    other = json.loads(row["body"])
                    if other["id"] != sid and other["phase"] != "DELETED" and (
                            other["name"].casefold() == body.name.casefold() or other["endpoint"] == body.endpoint):
                        raise HTTPException(409, "This name or endpoint is already registered")
        elif body.confirm_name != state["name"]:
            raise HTTPException(422, "Enter the exact connection name to confirm deletion")
        change_id = uuid4().hex
        snapshot = {k: copy.deepcopy(v) for k, v in state.items() if k not in ("history", "claim", "change")}
        archive = "mcp-revision:" + sid + ":" + change_id
        put(db, archive, snapshot)
        summary = {k: snapshot.get(k) for k in ("name", "description", "endpoint", "connection_id",
                                               "gateway_target_id", "registry_record_id", "workspaces")}
        state.setdefault("history", []).append({"revision": state.get("revision", 1), "archive_key": archive,
                                                "snapshot": summary, "action": kind, "requested_at": time.time()})
        state.update(config_scope="connection", stage="retire_registry", phase="DELETING",
                     change={"id": change_id, "kind": kind, "catalog_ids": ids,
                             "payload": body.model_dump(exclude={"idempotency_key", "expected_revision"}) if kind == "edit" else {},
                             "native_registry": not state.get("legacy_source", False)})
        if kind == "edit":
            state["change"]["payload"].update(tool_schema=schema, schema_source="supplied" if native_oauth else "discovered")
        state["config_digest"] = binding_digest(config, state)
        # Keep the old native operations as immutable history; retirement has
        # its own intents, and edited revisions get new native request tokens.
        state["operations"] = {}
        for cid in ids:
            row = db.select("components", where=[("id", "=", cid)]).fetchone()
            if not row:
                continue  # Grants may have been prepared before catalog activation.
            item = json.loads(row["body"])
            item.update(approved=False, execution_ready=False, management_lock=change_id)
            db.update("components", {"body": json.dumps(item)}, where=[("id", "=", cid)])
        if source == "servers":
            original = get(db, "mcp-server:" + sid)
            original["management_adopted"] = True
            put(db, "mcp-server:" + sid, original)
        response = service.job(db, state, actor, session_hash)
        put(db, key, {"digest": digest([kind, payload]), "response": response})
        service.audit(db, actor["id"], "mcp_connection_" + kind + "_requested", sid,
                      {"revision": state.get("revision", 1), "change_id": change_id, "payload": state["change"]["payload"]})
        return response
    return service.tx(reserve)


def clear_catalog(db, ids):
    """Bound each worker transaction, including removal of workspace grants."""
    grants = [dict(r) for r in db.select("grants") if r["component"] in ids]
    if grants:
        for row in grants[:16]:
            db.delete("grants", where=[("persona", "=", row["persona"]), ("component", "=", row["component"])])
            for workspace in ("research", "operations", "platform"):
                key = grant_scope({"id": row["persona"], "workspace": workspace}, row["component"])
                db.delete("settings", where=[("key", "=", key)])
        return False
    remaining = [r["id"] for r in db.select("components") if r["id"] in ids]
    for cid in remaining[:20]:
        db.delete("components", where=[("id", "=", cid)])
    return len(remaining) <= 20


def finish(service, db, state):
    from .mcp_onboarding import binding_digest
    change = state["change"]
    if not clear_catalog(db, change["catalog_ids"]):
        return
    state["revision"] = state.get("revision", 1) + 1
    state.pop("failure_code", None)
    state.pop("failure_context", None)
    if change["kind"] == "delete":
        state.update(phase="DELETED", stage="deleted", deleted_at=time.time())
        service.audit(db, state["requester"], "mcp_connection_deleted", state["id"], {"revision": state["revision"]})
    else:
        state.update(**change["payload"], phase="CONNECTING", stage="connect",
                     target_name="studio-remote-" + state["id"][:12] + "-r" + str(state["revision"]), operations={})
        for key in ("tools", "selected_tools", "discovery_digest", "gateway_target_id", "registry_record_id",
                    "registry_record_arn", "legacy_source", "publication_catalog_ids"):
            state.pop(key, None)
        state["config_digest"] = binding_digest(service.config(db), state)
    state.pop("change")
