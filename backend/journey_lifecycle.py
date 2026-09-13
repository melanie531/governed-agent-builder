"""Two-step deletion of one agent's resources; shared platform resources survive."""
import json
import secrets
import time

from fastapi import HTTPException

from foundation_harness.config import digest
from .foundation_runs import get, put


def deletion_key(agent_id):
    return "journey-deletion:" + agent_id


def ensure_available(db, agent_id):
    deletion = get(db, deletion_key(agent_id))
    if deletion:
        raise HTTPException(409, "This agent is being deleted or has been deleted; only cleanup can be retried")


def ensure_idle(journey, db, agent_id):
    from .journey import TERMINAL
    if db.select("jobs", where=[("agent", "=", agent_id), ("stage", "not_in", sorted(TERMINAL))]).fetchone():
        raise HTTPException(409, "Wait for the current operation to finish before deleting this agent")


def preview(journey, actor, agent_id, request, session_hash):
    def prepare(db):
        agent, definition = journey.owned(db, actor, agent_id)
        if request.version != agent["current_version"]:
            raise HTTPException(409, "Reload this agent before confirming deletion")
        deletion = get(db, deletion_key(agent_id))
        if deletion and deletion["status"] != "DELETE_FAILED":
            raise HTTPException(409, "Deletion has already been requested")
        ensure_idle(journey, db, agent_id)
        versions = list(db.select("versions", where=[("agent", "=", agent_id)]))
        token = secrets.token_urlsafe(32)
        expires = time.time() + 300
        put(db, "journey-delete-confirm:" + digest(token),
            {"agent": agent_id, "owner": actor["id"], "workspace": actor["workspace"],
             "version": request.version, "digest": definition["digest"], "name": definition["name"],
             "session_hash": session_hash, "expires": expires, "job_id": None})
        return {"confirmation_token": token, "expires": expires, "name": definition["name"],
                "versions": len(versions),
                "resources": ["AgentCore Runtimes for every saved version", "Agent manifests, chat and evaluation data",
                              "Agent execution log streams"],
                "retained": ["Shared Gateway and MCP targets", "AI Catalog, models and platform Foundation", "Deletion audit receipt"]}
    return journey.transaction(prepare)


def confirm(journey, actor, agent_id, request, session_hash):
    def commit(db):
        _, definition = journey.owned(db, actor, agent_id)
        key = "journey-delete-confirm:" + digest(request.confirmation_token)
        confirmation = get(db, key)
        if (not confirmation or confirmation["agent"] != agent_id or confirmation["owner"] != actor["id"]
                or confirmation["workspace"] != actor["workspace"] or confirmation["session_hash"] != session_hash
                or confirmation["version"] != request.version or confirmation["digest"] != definition["digest"]
                or not secrets.compare_digest(request.confirm_name, confirmation["name"])):
            raise HTTPException(403, "Deletion confirmation does not match this agent, version and signed-in session")
        if confirmation["job_id"]:
            return {"job_id": confirmation["job_id"]}
        if confirmation["expires"] < time.time():
            raise HTTPException(409, "Deletion confirmation expired; review and confirm again")
        ensure_idle(journey, db, agent_id)
        prior = get(db, deletion_key(agent_id))
        if prior and prior["status"] != "DELETE_FAILED":
            raise HTTPException(409, "Deletion has already been requested")
        plans = []
        prefixes = []
        job_ids = [row["id"] for row in db.select("jobs", where=[("agent", "=", agent_id)])]
        for row in db.select("versions", where=[("agent", "=", agent_id)], order="version"):
            value = json.loads(row["body"])
            manifest = get(db, "journey-manifest:" + value["digest"])
            deployment = get(db, journey.deployment_key(value)) or {}
            plans.append({"version": row["version"], "digest": value["digest"],
                          "name": "gab_journey_" + digest([value["digest"], "deploy"])[:24],
                          "binding": deployment.get("binding")})
            if manifest:
                prefixes.append({"prefix": "journey/manifests/" + digest(manifest) + ".json", "exact": True})
            prefixes.append({"prefix": "journey/evidence/" + value["digest"] + "/", "exact": False})
        prefixes.extend({"prefix": "journey/evaluations/" + job_id + "/", "exact": False} for job_id in job_ids)
        job_id = journey.enqueue(db, actor, definition, "delete", request.idempotency_key, session_hash=session_hash)
        from .journey import job_state
        state = job_state(db, job_id)
        state.update(plans=plans, prefixes=prefixes, previous_jobs=job_ids, resource_index=0, prefix_index=0)
        journey.persist(db, state)
        confirmation["job_id"] = job_id
        put(db, key, confirmation)
        put(db, deletion_key(agent_id), {"status": "QUEUED", "job_id": job_id, "requested": time.time()})
        return {"job_id": job_id}
    return journey.transaction(commit)


def perform(journey, state):
    phase = state["phase"]
    if phase == "QUEUED":
        resources = journey.cloud.find_agent_runtimes(state["agent"], state["workspace"], state["plans"])
        return {"phase": "DELETE_RUNTIMES", "resources": resources}
    if phase == "DELETE_RUNTIMES":
        index = state["resource_index"]
        if index == len(state["resources"]):
            return {"phase": "DELETE_DATA"}
        if journey.cloud.delete_runtime(state["resources"][index]):
            return {"resource_index": index + 1}
        return {}
    if phase == "DELETE_DATA":
        index = state["prefix_index"]
        if index == len(state["prefixes"]):
            journey.cloud.delete_agent_log_streams(state["agent"], [p["version"] for p in state["plans"]])
            return {"phase": "DELETE_RECORDS"}
        if journey.cloud.purge_agent_objects(state["prefixes"][index]):
            return {"prefix_index": index + 1}
        return {}
    if phase == "DELETE_RECORDS":
        return journey.transaction(lambda db: scrub(journey, db, state))
    raise ValueError("Unknown deletion phase")


def scrub(journey, db, state):
    """Bound each DynamoDB transaction; preserve authority until final commit."""
    from .journey import PREFIX
    agent_id = state["agent"]
    digests = {plan["digest"] for plan in state["plans"]}
    previous_jobs = set(state["previous_jobs"])
    candidates = []
    for row in db.select("settings"):
        key = row["key"]
        matched = (key in {PREFIX + job_id for job_id in previous_jobs}
                   or key in {"journey-manifest:" + value for value in digests}
                   or any(key.startswith(prefix + agent_id + ":") for prefix in (
                       "journey-conversation:", "journey-chat:", "journey-invocation:",
                       "journey-evaluation:", "journey-deployment:")))
        if key.startswith(("journey-delete-confirm:", "journey-request:", "journey-save:")):
            value = json.loads(row["body"])
            matched = matched or value.get("agent") == agent_id or value.get("agent_id") == agent_id or value.get("job_id") in previous_jobs
        if matched:
            candidates.append(("settings", [("key", "=", key)]))
    for table in ("jobs", "job_authority", "events"):
        for row in db.select(table):
            if (row["job"] if table == "events" else row["id"]) in previous_jobs:
                candidates.append((table, [("id", "=", row["id"])]))
    for row in db.select("versions", where=[("agent", "=", agent_id)]):
        if row["version"] != state["version"]:
            candidates.append(("versions", [("agent", "=", agent_id), ("version", "=", row["version"])]))
    for table, where in candidates[:25]:
        db.delete(table, where=where)
    if candidates:
        return {}
    return {"phase": "DELETED", "completed": time.time()}


def finish(journey, db, state, definition):
    put(db, deletion_key(state["agent"]), {
        "status": state["phase"], "job_id": state["id"], "error": state.get("error"),
        "runtimes": len(state.get("resources", [])), "completed": state.get("completed")})
    if state["phase"] == "DELETED":
        # Keep only a tombstone and cleanup receipt, never the old prompt/data.
        tombstone = {key: definition[key] for key in (
            "agent_id", "version", "owner", "workspace", "name", "schema_version", "catalog_mode",
            "digest", "foundation_name", "foundation_id")}
        db.update("versions", {"body": json.dumps(tombstone)}, where=[
            ("agent", "=", state["agent"]), ("version", "=", state["version"])])
        state.pop("plans", None)
        state.pop("prefixes", None)
        state.pop("previous_jobs", None)
