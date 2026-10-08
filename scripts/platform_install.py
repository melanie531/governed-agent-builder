"""Initial provider-neutral Studio catalog and AgentCore integration.

This installer never creates a remote target, reads a provider secret, or invokes
an MCP tool. Administrators publish models and onboard their own MCP endpoints
through Studio after activation.
"""
import json
from pathlib import Path

from backend.dynamo_store import DynamoStore
from backend.foundation_runs import get, put
from backend.live_catalog import grant_scope
from backend.network_policy import validate_network
from foundation_harness.config import digest
from scripts import journey_platform as journey
from scripts.bootstrap_support import Journal, gateway, stack

ROOT = Path(__file__).resolve().parents[1]
WORKSPACES = ["research", "operations"]


def starter_catalog():
    items = []
    for skill in json.loads((ROOT / "examples/journey/skills.json").read_text()):
        binding = {"type": "instructions", "instructions": skill["instruction"],
                   "content_digest": digest(skill["instruction"])}
        items.append({
            "id": skill["id"], "name": skill["name"], "kind": "skill", "description": skill["description"],
            "binding": binding, "binding_digest": digest(binding), "catalog": "journey", "version": "1",
            "approved": True, "fixture": False, "external": False, "origin": "AI Catalog",
            "execution_ready": True, "integration_ready": True, "supported": True, "requestable": True,
            "provider": "Platform skill library", "protocol": "instructions",
            "discoverable_workspaces": WORKSPACES, "default_grant_workspaces": WORKSPACES})
    templates = json.loads((ROOT / "examples/journey/starter-templates.json").read_text())
    for entry in templates:
        entry.update(catalog="journey", version="1", approved=True, workspaces=WORKSPACES, requires_tool=False)
    return items, templates


def publish_catalog(db, settings, items, templates):
    """Initial publication only; never repurpose an operator's existing catalog."""
    current = get(db, "journey-platform")
    if current is not None and current != settings:
        raise RuntimeError("Platform is already configured; use the existing catalog management UI")
    for table, entries in (("components", items), ("foundations", templates)):
        for entry in entries:
            row = db.select(table, where=[("id", "=", entry["id"])]).fetchone()
            if row:
                prior = json.loads(row["body"])
                if prior.get("catalog") == "journey" and prior != entry:
                    raise RuntimeError("Existing catalog entry changed; refusing bootstrap overwrite")
            db.insert(table, {"id": entry["id"], "body": json.dumps(entry)}, upsert=True)
    for row in db.select("principals"):
        actor = json.loads(row["body"])
        if actor["workspace"] in WORKSPACES:
            for entry in items:
                db.insert("grants", {"persona": actor["id"], "component": entry["id"]}, ignore=True)
                put(db, grant_scope(actor, entry["id"]), True)
    put(db, "journey-platform", settings)


def prepare(target, *, worker_memory_size=None):
    if worker_memory_size is not None and (
            type(worker_memory_size) is not int or not 512 <= worker_memory_size <= 10240):
        raise ValueError("Worker memory must be an integer from 512 to 10240 MiB")
    existing = target.state.get("journeyPlatform")
    if (existing and worker_memory_size is not None
            and existing.get("worker_memory_size", 1024) != worker_memory_size):
        raise RuntimeError("Installed worker memory differs; use a reviewed infrastructure update")
    store = DynamoStore(target.state["app"]["outputs"]["StateTable"], target.session.resource("dynamodb"))
    if target.state.get("journeyPlatform"):
        with store.tx() as db:
            if get(db, "journey-platform") != target.state["journeyPlatform"]:
                raise RuntimeError("Live platform settings differ; reconcile the bound state first")
        print("Platform already configured; existing catalog and connections preserved", flush=True)
        return

    # Explicit choice from scripts/configure_agent_network.py; fresh installs without one stay PUBLIC.
    network = validate_network(target.state.get("agentNetwork") or {"networkMode": "PUBLIC"})
    bucket, key, artifact = journey.upload(target)
    outputs = stack(target, bucket, key)
    control = target.session.client("bedrock-agentcore-control")
    gw = gateway(target, control, outputs)
    evaluator = control.get_evaluator(evaluatorId="Builtin.Correctness")
    settings = {
        "enabled": True, "admin_enabled": True,
        **{k: target.binding[k] for k in ("account", "region")},
        "foundation": json.loads((ROOT / "examples/journey/foundation.json").read_text()),
        "artifact": artifact, "runtime_role": outputs["RuntimeRole"],
        "gateway_id": gw["id"], "gateway_url": gw["url"],
        "bucket": outputs["EvidenceBucket"], "log_group": outputs["TraceLogGroup"],
        "network": network, "evaluator_id": evaluator["evaluatorId"],
        "evaluator_arn": evaluator["evaluatorArn"],
    }
    if worker_memory_size is not None:
        settings["worker_memory_size"] = worker_memory_size
    items, templates = starter_catalog()
    publication = {"settings": settings, "items": items, "templates": templates}
    marker = "platform-install:" + digest(publication)

    def read(record):
        with store.tx() as db:
            return get(db, marker)

    def write(token):
        receipt = {"digest": digest(publication), "request_token": token}
        with store.tx() as db:
            publish_catalog(db, settings, items, templates)
            put(db, marker, receipt)
        return receipt

    Journal(target).run("initial-catalog", publication, write, read)
    target.save("journeyPlatform", settings)
    print("Prepared empty Gateway and generic catalog; publish models and onboard MCPs in Studio", flush=True)
