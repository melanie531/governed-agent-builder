"""Shared AI Catalog authority, read from backend storage on every request.

Example skills/templates are deployment input data, never application constants.
Only platform administration can publish records and execution bindings.
"""
import json

from fastapi import HTTPException
from pydantic import Field

from foundation_harness.config import digest
from .journey_schema import Strict
from .live_catalog import projection


class SkillPublication(Strict):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z][a-zA-Z0-9_.:-]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(max_length=1000)
    instructions: str = Field(min_length=1, max_length=8000)
    workspaces: list[str] = Field(min_length=1, max_length=10)
    expected_version: str | None = None


def records(db):
    return [item for row in db.select("components")
            if (item := json.loads(row["body"])).get("catalog") == "journey"]


def templates(db):
    return [item for row in db.select("foundations")
            if (item := json.loads(row["body"])).get("catalog") == "journey" and item.get("approved")]


def template(db, template_id):
    result = next((item for item in templates(db) if item["id"] == template_id), None)
    if result is None:
        raise HTTPException(409, "This template is unavailable; refresh the catalog")
    return result


def choices(db, actor):
    return [public for item in records(db) if (public := projection(db, actor, item)) is not None]


def resolve(db, actor, selected, versions=None):
    """Resolve exact versions from the same authority used by GET /api/catalog."""
    catalog = {item["id"]: item for item in records(db)}
    resolved = {}
    for kind, ids in selected.items():
        for cid in ids:
            item = catalog.get(cid)
            visible = projection(db, actor, item) if item else None
            if not visible or item["kind"] != kind:
                raise HTTPException(403, "A selected capability is no longer available in your catalog")
            if not visible["granted"] or not item.get("execution_ready") or not item.get("binding"):
                raise HTTPException(403, "A selected capability is not approved for execution in your workspace")
            if versions is not None and versions.get(cid) != item["version"]:
                raise HTTPException(409, "A selected capability changed version; refresh the catalog and revise the agent")
            if digest(item["binding"]) != item.get("binding_digest"):
                raise HTTPException(503, "Catalog execution binding is invalid")
            if cid in resolved:
                raise HTTPException(422, "Duplicate capability selection")
            resolved[cid] = item
    if versions is not None and set(versions) != set(resolved):
        raise HTTPException(422, "Selected capabilities and pinned versions must match exactly")
    return resolved


def publish_skill(db, actor, publication):
    if actor["role"] != "admin":
        raise HTTPException(403, "Platform admin required")
    prior = next((item for item in records(db) if item["id"] == publication.id), None)
    if prior and (prior["kind"] != "skill" or publication.expected_version != prior["version"]):
        raise HTTPException(409, "Catalog changed; reload the current skill version")
    if not prior and publication.expected_version is not None:
        raise HTTPException(409, "Skill does not exist")
    binding = {"type": "instructions", "instructions": publication.instructions,
               "content_digest": digest(publication.instructions)}
    version = str(int(prior["version"]) + 1) if prior else "1"
    item = {
        "id": publication.id, "catalog": "journey", "name": publication.name,
        "description": publication.description, "kind": "skill", "version": version,
        "provider": "Platform skill library", "origin": "AI Catalog", "approved": True,
        "fixture": False, "external": False, "protocol": "instructions",
        "discoverable_workspaces": publication.workspaces, "requestable": True,
        "execution_ready": True, "integration_ready": True, "supported": True,
        "data_handling": "Platform-published instructions; no executable code.",
        "binding": binding, "binding_digest": digest(binding),
    }
    db.insert("components", {"id": item["id"], "body": json.dumps(item)}, upsert=True)
    return {key: item[key] for key in ("id", "name", "version", "description")}
