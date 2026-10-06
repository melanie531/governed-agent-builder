"""Administrator governance, Registry publication and operational metadata APIs."""
import json
import time

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, field_validator

from foundation_harness.config import digest
from .foundation_runs import get, put
from .journey_catalog import records
from .journey_schema import Strict
from .platform_metrics import overview
from .platform_cloud import registry_descriptor


class Reasoned(Strict):
    reason: str = Field(min_length=5, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_content(cls, value):
        if len(value.strip()) < 5:
            raise ValueError("Enter a decision reason")
        return value.strip()


class Revision(Reasoned):
    version: str = Field(min_length=1, max_length=30)


class Availability(Revision):
    approved: bool
    workspaces: list[str] = Field(min_length=1, max_length=20)


class RegistryDecision(Revision):
    approve: bool


class ModelRegistration(Reasoned):
    model_id: str = Field(min_length=1, max_length=300)
    workspaces: list[str] = Field(min_length=1, max_length=20)


class EvaluationPolicy(Reasoned):
    version: int = Field(ge=1)
    minimum_evaluation_score: float = Field(ge=0, le=1)


def router(store, who, cloud=None):
    routes = APIRouter(prefix="/api/admin/platform")

    def tx(operation):
        for attempt in range(6):
            try:
                with store.tx() as db:
                    return operation(db)
            except HTTPException as exc:
                if exc.status_code != 409 or exc.detail != "Concurrent governance update; reload and retry" or attempt == 5:
                    raise
                time.sleep(.025 * (attempt + 1))

    def native(operation):
        if cloud is None:
            raise HTTPException(503, "Native administrator integration is not configured")
        try:
            return operation()
        except (ClientError, BotoCoreError) as exc:
            code = exc.response["Error"]["Code"] if isinstance(exc, ClientError) else type(exc).__name__
            raise HTTPException(503, "AWS administrator source unavailable: " + code) from None

    def audit(db, actor, action, resource, detail):
        db.insert("audit", {"actor": actor["id"], "action": action, "resource": resource,
                           "detail": json.dumps(detail), "created": time.time()})

    def workspaces(db):
        return sorted({workspace for row in db.select("foundations")
                       for workspace in json.loads(row["body"]).get("workspaces", [])})

    def check_workspaces(db, selected):
        if len(set(selected)) != len(selected) or not set(selected).issubset(workspaces(db)):
            raise HTTPException(422, "Select configured platform workspaces")

    def component(db, cid, version=None):
        item = next((item for item in records(db) if item["id"] == cid), None)
        if item is None:
            raise HTTPException(404, "Catalog capability not found")
        if version is not None and item["version"] != version:
            raise HTTPException(409, "Catalog version changed; refresh before continuing")
        return item

    def binding(item):
        value = item.get("registry")
        if not value or value["binding_digest"] != item["binding_digest"]:
            raise HTTPException(409, "Register the current capability binding first")
        return value

    def verify_record(item, record):
        registered = binding(item)
        if record.get("recordArn") != registered["arn"] or record.get("recordVersion") != registered["version"]:
            raise HTTPException(409, "Registry record identity or version changed; register a reviewed version")
        if registered.get("descriptor_type") == "mcpServer":
            try:
                descriptor = record["descriptors"]["mcpServer"]
                tools = json.loads(descriptor["additionalData"]["tools"]["data"])["tools"]
                server = json.loads(descriptor["data"])
                from foundation_harness.config import digest
                if (record["recordType"] != "MCP" or digest(tools) != registered["discovery_digest"]
                        or server["remotes"] != [{"type": "streamable-http", "url": registered["endpoint"]}]):
                    raise ValueError()
            except (KeyError, ValueError, TypeError):
                raise HTTPException(409, "Native MCP record changed; review the connection again") from None
            return
        try:
            modern = registered.get("descriptor_type") == "custom"
            if modern and record["recordType"] != "CUSTOM":
                raise ValueError()
            descriptor = json.loads(record["descriptors"]["custom"]["data" if modern else "inlineContent"])
        except (KeyError, ValueError, TypeError):
            raise HTTPException(409, "Registry descriptor does not match the Catalog") from None
        expected = registry_descriptor({**item, "version": binding(item)["version"]})
        if descriptor != expected:
            raise HTTPException(409, "Registry descriptor changed; register a reviewed version")

    @routes.get("/overview")
    def get_overview(request: Request):
        who(request, True)
        return tx(overview)

    @routes.get("/policy")
    def policy(request: Request):
        who(request, True)
        return tx(lambda db: get(db, "journey-governance-policy") or {"version": 1, "minimum_evaluation_score": .7})

    @routes.post("/policy")
    def update_policy(body: EvaluationPolicy, request: Request):
        actor = who(request, True)
        def save(db):
            previous = get(db, "journey-governance-policy") or {"version": 1, "minimum_evaluation_score": .7}
            if previous["version"] != body.version:
                raise HTTPException(409, "Policy changed; refresh before saving")
            current = {"version": body.version + 1, "minimum_evaluation_score": body.minimum_evaluation_score}
            put(db, "journey-governance-policy", current)
            audit(db, actor, "journey_policy_changed", "evaluation", {**current, "reason": body.reason})
            return current
        return tx(save)

    @routes.get("/catalog")
    def get_catalog(request: Request):
        who(request, True)
        def read(db):
            fields = ("id", "name", "description", "kind", "version", "provider", "approved",
                      "discoverable_workspaces", "execution_ready", "registry", "parent_id", "model_id")
            return {"workspaces": workspaces(db),
                    "items": [{key: item[key] for key in fields if key in item} for item in records(db)]}
        return tx(read)

    @routes.get("/models/discovery")
    def discover_models(request: Request):
        who(request, True)
        return native(lambda: cloud.models())

    @routes.post("/models", status_code=201)
    def register_model(body: ModelRegistration, request: Request):
        actor = who(request, True)
        tx(lambda db: check_workspaces(db, body.workspaces))
        model = next((item for item in native(lambda: cloud.models()) if item["id"] == body.model_id), None)
        if model is None:
            raise HTTPException(422, "Select a model from current Bedrock discovery")
        cid = "model-" + digest(body.model_id)[:24]
        def save(db):
            check_workspaces(db, body.workspaces)
            prior = next((item for item in records(db) if item.get("binding", {}).get("model_id") == body.model_id), None)
            if prior:
                return {"id": prior["id"], "reused": True}
            route = {"type": "bedrock-converse", "model_id": body.model_id, "supports_temperature": False}
            item = {"id": cid, "catalog": "journey", "kind": "model", "name": model["name"],
                    "description": "Bedrock model registered for platform review.", "provider": model["provider"],
                    "version": "1", "approved": False, "execution_ready": False, "integration_ready": True,
                    "supported": True, "fixture": False, "external": False, "origin": "AI Catalog",
                    "model_id": body.model_id, "binding": route, "binding_digest": digest(route),
                    "discoverable_workspaces": body.workspaces, "default_grant_workspaces": [],
                    "requestable": True, "data_handling": "Amazon Bedrock inference."}
            db.insert("components", {"id": cid, "body": json.dumps(item)})
            audit(db, actor, "model_registered", cid, {"model_id": body.model_id, "reason": body.reason})
            return {"id": cid, "reused": False}
        return tx(save)

    @routes.get("/registry")
    def registry(request: Request):
        who(request, True)
        return native(lambda: cloud.registry())

    @routes.post("/catalog/{cid}/validate-model")
    def validate_model(cid: str, body: Revision, request: Request):
        actor = who(request, True)
        def claim(db):
            item = component(db, cid, body.version)
            if item["kind"] != "model":
                raise HTTPException(422, "Select a model connection")
            previous = get(db, "platform-model-validation:" + cid) or {}
            if previous.get("phase") == "RUNNING" and previous["started"] > time.time() - 120:
                raise HTTPException(409, "Model validation is already running")
            stamp = time.time()
            put(db, "platform-model-validation:" + cid, {"phase": "RUNNING", "started": stamp})
            return item, stamp
        item, stamp = tx(claim)
        try:
            proof = native(lambda: cloud.validate_model(item["binding"]["model_id"]))
        except HTTPException:
            tx(lambda db: put(db, "platform-model-validation:" + cid, {"phase": "FAILED", "started": stamp}))
            raise
        def save(db):
            current = component(db, cid, body.version)
            if current["binding_digest"] != item["binding_digest"]:
                raise HTTPException(409, "Model binding changed during validation")
            current.update(execution_ready=True, connection_validation=proof)
            db.insert("components", {"id": cid, "body": json.dumps(current)}, upsert=True)
            put(db, "platform-model-validation:" + cid, {"phase": "SUCCEEDED", "started": stamp, **proof})
            audit(db, actor, "model_validated", cid, {**proof, "reason": body.reason})
            return {"status": "VALIDATED"}
        return tx(save)

    @routes.post("/catalog/{cid}/register")
    def register(cid: str, body: Revision, request: Request):
        actor = who(request, True)
        item = tx(lambda db: component(db, cid, body.version))
        receipt = native(lambda: cloud.register(item))
        def save(db):
            current = component(db, cid, body.version)
            if current["binding_digest"] != item["binding_digest"]:
                raise HTTPException(409, "Catalog binding changed during registration")
            current.update(registry=receipt, managed_by="platform-admin")
            db.insert("components", {"id": cid, "body": json.dumps(current)}, upsert=True)
            audit(db, actor, "registry_registered", cid, {"record_arn": receipt["arn"], "reason": body.reason})
            return receipt
        return tx(save)

    @routes.get("/catalog/{cid}/registry")
    def registry_record(cid: str, request: Request):
        who(request, True)
        item = tx(lambda db: component(db, cid))
        record = native(lambda: cloud.record(binding(item)))
        verify_record(item, record)
        return {key: record[key] for key in ("recordArn", "recordVersion", "status", "statusReason") if key in record}

    @routes.post("/catalog/{cid}/submit")
    def submit(cid: str, body: Revision, request: Request):
        actor = who(request, True)
        item = tx(lambda db: component(db, cid, body.version))
        record = native(lambda: cloud.record(binding(item)))
        verify_record(item, record)
        if record["status"] != "DRAFT":
            raise HTTPException(409, "Only a draft Registry record can be submitted")
        status = native(lambda: cloud.submit(binding(item)))
        tx(lambda db: audit(db, actor, "registry_submitted", cid, {"status": status, "reason": body.reason}))
        return {"status": status}

    @routes.post("/catalog/{cid}/decision")
    def decision(cid: str, body: RegistryDecision, request: Request):
        actor = who(request, True)
        item = tx(lambda db: component(db, cid, body.version))
        record = native(lambda: cloud.record(binding(item)))
        verify_record(item, record)
        if record["status"] != "PENDING_APPROVAL":
            raise HTTPException(409, "This Registry record is not pending approval")
        status = native(lambda: cloud.decide(binding(item), body.approve, body.reason))
        tx(lambda db: audit(db, actor, "registry_decided", cid, {"status": status, "reason": body.reason}))
        return {"status": status}

    @routes.post("/catalog/{cid}/availability")
    def availability(cid: str, body: Availability, request: Request):
        actor = who(request, True)
        item = tx(lambda db: component(db, cid, body.version))
        if body.approved:
            record = native(lambda: cloud.record(binding(item)))
            verify_record(item, record)
            if record["status"] != "APPROVED":
                raise HTTPException(409, "Approve the native Registry record before publishing")
            if not item.get("execution_ready"):
                raise HTTPException(409, "Validate the model connection before publishing")
        def save(db):
            current = component(db, cid, body.version)
            check_workspaces(db, body.workspaces)
            if current["binding_digest"] != item["binding_digest"] or current.get("registry") != item.get("registry"):
                raise HTTPException(409, "Catalog registration changed; refresh")
            prior = dict(current)
            current.update(approved=body.approved, discoverable_workspaces=body.workspaces, managed_by="platform-admin",
                           version=str(int(current["version"]) + 1))
            # Do not auto-grant a newly approved route to every workspace member.
            current["default_grant_workspaces"] = []
            db.insert("catalog_history", {"resource": cid, "body": json.dumps(prior), "created": time.time()})
            db.insert("components", {"id": cid, "body": json.dumps(current)}, upsert=True)
            audit(db, actor, "catalog_published" if body.approved else "catalog_withdrawn", cid,
                  {"version": current["version"], "workspaces": body.workspaces, "reason": body.reason})
            return {"version": current["version"], "approved": current["approved"]}
        return tx(save)

    @routes.get("/performance/models")
    def model_metrics(request: Request):
        who(request, True)
        ids = tx(lambda db: sorted({item["binding"]["model_id"] for item in records(db) if item["kind"] == "model"}))
        return native(lambda: cloud.model_metrics(ids))

    @routes.get("/costs")
    def costs(request: Request):
        who(request, True)
        return native(lambda: cloud.costs())

    return routes
