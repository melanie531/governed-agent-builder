import copy
import json
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.foundation_runs import get, put
from backend.store import Store
from backend.platform_metrics import overview
from backend.platform_cloud import registry_descriptor
from foundation_harness.config import digest
from tests.conftest import login, ORIGIN
from tests.journey_support import make_journey


class Native:
    def __init__(self):
        self.rows = {}
        self.calls = []
        self.modern = False

    def models(self):
        return [{"id": "synthetic.model", "name": "Synthetic model", "provider": "Synthetic", "type": "Foundation model"}]

    def register(self, item):
        arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:registry/abcdefghijkl/record/" + digest(item["id"])[:12]
        if self.modern:
            arn = arn.replace(":bedrock-agentcore:", ":agent-registry:")
        self.rows[arn] = {"recordArn": arn, "recordVersion": item["version"], "status": "DRAFT",
                         "descriptors": {"custom": {
                             "data" if self.modern else "inlineContent": json.dumps(registry_descriptor(item))}}}
        if self.modern:
            self.rows[arn]["recordType"] = "CUSTOM"
        self.calls.append("register")
        return {"arn": arn, "status": "CREATING", "version": item["version"], "binding_digest": item["binding_digest"],
                **({"descriptor_type": "custom"} if self.modern else {})}

    def record(self, binding):
        return copy.deepcopy(self.rows[binding["arn"]])

    def submit(self, binding):
        self.rows[binding["arn"]]["status"] = "PENDING_APPROVAL"
        return "PENDING_APPROVAL"

    def decide(self, binding, approve, reason):
        self.rows[binding["arn"]]["status"] = "APPROVED" if approve else "REJECTED"
        return self.rows[binding["arn"]]["status"]

    def validate_model(self, model_id):
        self.calls.append("validate")
        return {"request_id": "synthetic-request", "validated_at": time.time()}

    def model_metrics(self, ids):
        return {"scope": "Synthetic browser fixture. Account-wide model usage.",
                "start": "2026-09-12T00:00:00Z", "end": "2026-09-13T00:00:00Z",
                "models": [{"model_id": cid, "invocations": 7, "latency_p50_ms": 250, "latency_p95_ms": 800,
                            "client_errors": 0, "server_errors": 1, "input_tokens": 120, "output_tokens": 40,
                            "throttles": None} for cid in ids]}

    def costs(self):
        return {"status": "UNAVAILABLE", "reason": "Synthetic fixture: project allocation is not active."}

    def registry(self):
        return {"name": "Synthetic Registry", "status": "READY", "approvalConfiguration": {"autoApproval": False}}


@pytest.fixture
def platform(tmp_path):
    store = Store(str(tmp_path / "platform.sqlite"))
    journey, _ = make_journey(store)
    native = Native()
    app = create_app(repository=store, demo_mode=True, worker_enabled=False, journey=journey, admin_cloud=native)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, store, native, journey


@pytest.mark.parametrize("path", ["/overview", "/catalog", "/registry", "/models/discovery", "/performance/models", "/costs"])
def test_business_cannot_read_admin_sources(platform, path):
    client, _, native, _ = platform
    login(client)
    assert client.get("/api/admin/platform" + path).status_code == 403
    assert native.calls == []


@pytest.mark.parametrize("modern", [False, True])
def test_model_registry_approval_publish_grant_and_withdraw(platform, modern):
    client, store, native, _ = platform
    native.modern = modern
    login(client, "admin")
    created = client.post("/api/admin/platform/models", json={
        "model_id": "synthetic.model", "workspaces": ["research"], "reason": "Synthetic model review"})
    assert created.status_code == 201
    cid = created.json()["id"]
    base = "/api/admin/platform/catalog/" + cid
    revision = {"version": "1", "reason": "Reviewed for research"}
    publication = {**revision, "approved": True, "workspaces": ["research"]}
    assert client.post(base + "/availability", json=publication).status_code == 409
    assert client.post(base + "/register", json=revision).status_code == 200
    assert client.get(base + "/registry").json()["status"] == "DRAFT"
    assert client.post(base + "/decision", json={**revision, "approve": True}).status_code == 409
    assert client.post(base + "/submit", json=revision).status_code == 200
    assert client.post(base + "/decision", json={**revision, "approve": True}).status_code == 200
    assert client.post(base + "/availability", json=publication).status_code == 409
    assert client.post(base + "/validate-model", json=revision).status_code == 200
    assert client.post(base + "/availability", json=publication).json() == {"version": "2", "approved": True}
    assert client.post(base + "/availability", json=publication).status_code == 409
    login(client)
    detail = client.get("/api/catalog/" + cid).json()
    assert detail["execution_ready"] and detail["requestable"] and not detail["granted"]
    request = client.post("/api/requests", json={"component_id": cid, "reason": "Use reviewed model for research"})
    assert request.status_code == 201
    login(client, "admin")
    assert client.post("/api/admin/requests/" + request.json()["id"] + "/decision",
                       json={"approve": True, "reason": "Approved research access"}).status_code == 200
    login(client)
    assert client.get("/api/catalog/" + cid).json()["usable"]
    login(client, "admin")
    assert client.post(base + "/availability", json={**publication, "version": "2", "approved": False}).status_code == 200
    login(client)
    assert client.get("/api/catalog/" + cid).status_code == 404
    with store.tx() as db:
        assert {row["action"] for row in db.select("audit")} >= {"registry_registered", "registry_decided", "model_validated", "catalog_published", "catalog_withdrawn"}


def test_validation_http_exception_surfaces_detail_and_records_failed_phase(platform):
    client, store, native, _ = platform
    detail = "Bedrock rejected this model ID: synthetic message. Register the model's inference profile from discovery instead."
    def reject(model_id):
        raise HTTPException(422, detail)
    native.validate_model = reject
    login(client, "admin")
    created = client.post("/api/admin/platform/models", json={
        "model_id": "synthetic.model", "workspaces": ["research"], "reason": "Validation failure test"})
    cid = created.json()["id"]
    response = client.post("/api/admin/platform/catalog/" + cid + "/validate-model",
                           json={"version": "1", "reason": "Validation failure test"})
    assert response.status_code == 422
    assert response.json()["detail"] == detail
    with store.tx() as db:
        assert get(db, "platform-model-validation:" + cid)["phase"] == "FAILED"


@pytest.mark.parametrize("model_id", ["global.not-real", "us.anthropic.claude-opus-5-5"])
def test_model_registration_rejects_ids_absent_from_global_discovery(platform, model_id):
    client, _, native, _ = platform
    native.models = lambda: [{"id": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
                              "name": "Global Claude Haiku 4.5", "provider": "Amazon Bedrock",
                              "type": "Inference profile"}]
    login(client, "admin")
    response = client.post("/api/admin/platform/models", json={
        "model_id": model_id, "workspaces": ["research"], "reason": "Global policy test"})
    assert response.status_code == 422
    assert response.json()["detail"] == "Select a model from current Bedrock discovery"


@pytest.mark.parametrize("field,value", [("schema", "tampered"), ("target_id", "different-target"), ("recordVersion", "changed")])
@pytest.mark.parametrize("modern", [False, True])
def test_registry_descriptor_drift_blocks_approval(platform, field, value, modern):
    client, _, native, _ = platform
    native.modern = modern
    login(client, "admin")
    base = "/api/admin/platform/catalog/mcp-tavily"
    revision = {"version": "1", "reason": "Synthetic approval"}
    assert client.post(base + "/register", json=revision).status_code == 200
    descriptor = next(iter(native.rows.values()))["descriptors"]["custom"]
    if field == "recordVersion":
        next(iter(native.rows.values()))[field] = value
    else:
        key = "data" if modern else "inlineContent"
        descriptor[key] = json.dumps({**json.loads(descriptor[key]), field: value})
    assert client.post(base + "/submit", json=revision).status_code == 409


def test_native_custom_record_type_cannot_drift(platform):
    client, _, native, _ = platform
    native.modern = True
    login(client, "admin")
    base = "/api/admin/platform/catalog/mcp-tavily"
    revision = {"version": "1", "reason": "Synthetic approval"}
    assert client.post(base + "/register", json=revision).status_code == 200
    assert client.get(base + "/registry").status_code == 200
    next(iter(native.rows.values()))["recordType"] = "MCP"
    assert client.post(base + "/submit", json=revision).status_code == 409


def test_model_registration_rejects_unlisted_and_wrong_workspace(platform):
    client, _, native, _ = platform
    login(client, "admin")
    body = {"model_id": "synthetic.model", "workspaces": ["research"], "reason": "Model registration test"}
    assert client.post("/api/admin/platform/models", json={**body, "model_id": "arbitrary"}).status_code == 422
    assert client.post("/api/admin/platform/models", json={**body, "workspaces": ["unapproved"]}).status_code == 422
    login(client)
    assert client.post("/api/admin/platform/models", json=body).status_code == 403
    assert native.calls == []


def test_tool_request_reaches_admin_and_decision_is_audited(platform):
    client, store, _, _ = platform
    login(client)
    created = client.post("/api/tool-requests", json={"title": "CRM", "idempotency_key": "admin-handoff"}).json()
    login(client, "admin")
    assert client.get("/api/admin/platform/overview").json()["pending_tool_requests"] == 1
    assert client.get("/api/tool-requests").json()[0]["id"] == created["id"]
    assert client.post(f"/api/admin/tool-requests/{created['id']}/response", json={
        "version": 1, "status": "IN_REVIEW", "response": "Reviewing CRM requirements."}).status_code == 200
    with store.tx() as db:
        assert [row["action"] for row in db.select("audit") if row["resource"] == created["id"]] == ["tool_requested", "tool_request_responded"]


def test_performance_counts_known_outcomes_and_excludes_private_content(platform):
    _, store, _, _ = platform
    with store.tx() as db:
        db.insert("agents", {"id": "agent", "owner": "alex", "workspace": "research", "current_version": 1, "created": time.time()})
        db.insert("versions", {"agent": "agent", "version": 1, "digest": "synthetic", "created": time.time(),
                              "body": json.dumps({"catalog_mode": "journey", "name": "Synthetic agent", "prompt": "PRIVATE",
                                                  "dataset": ["PRIVATE"], "resolved_model_id": "synthetic.model"})})
        for index, phase in enumerate(["SUCCEEDED", "SUCCEEDED", "FAILED", "UNKNOWN", "QUEUED"]):
            put(db, "journey-job:" + str(index), {"agent": "agent", "kind": "invoke", "phase": phase, "created": time.time(),
                "latency_ms": 100 * (index + 1), "output": "PRIVATE", "input": "PRIVATE"})
        put(db, "journey-job:eval", {"agent": "agent", "kind": "evaluation", "phase": "FAILED", "created": time.time()})
        result = overview(db)
    row = result["agents"][0]
    assert row["invocations"] == 5 and row["error_rate"] == 1 / 3
    assert row["unknown"] == 1 and row["pending"] == 1
    assert row["latency_p50_ms"] == 100 and row["latency_p95_ms"] == 200
    assert "PRIVATE" not in json.dumps(result)


def test_policy_has_stale_write_fence_and_is_admin_only(platform):
    client, _, _, _ = platform
    login(client)
    body = {"version": 1, "minimum_evaluation_score": .8, "reason": "Increase evaluation quality"}
    assert client.post("/api/admin/platform/policy", json=body).status_code == 403
    login(client, "admin")
    assert client.post("/api/admin/platform/policy", json=body).json()["version"] == 2
    assert client.post("/api/admin/platform/policy", json=body).status_code == 409
    login(client)
    assert client.get("/api/journey/options").json()["minimum_evaluation_score"] == .8


def test_legacy_approval_endpoint_cannot_bypass_registry(platform):
    client, _, _, _ = platform
    login(client, "admin")
    assert client.post("/api/admin/catalog/components/bedrock-claude", json={"approved": True}).status_code == 409


def test_current_evaluation_policy_applies_only_when_dataset_is_supplied(platform):
    from backend.catalog import PERSONAS
    from tests.journey_support import definition
    client, store, _, journey = platform
    login(client, "admin")
    assert client.post("/api/admin/platform/policy", json={
        "version": 1, "minimum_evaluation_score": .9, "reason": "Review model quality"}).status_code == 200
    empty = definition(journey)
    with store.tx() as db:
        journey.validate(db, PERSONAS["alex"], empty)
        with pytest.raises(HTTPException) as error:
            journey.validate(db, PERSONAS["alex"], {**empty, "dataset": [{"input": "Synthetic case"}]})
        assert error.value.status_code == 409
