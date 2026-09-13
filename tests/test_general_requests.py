"""General platform requests: submitted outside the AI Catalog, handled by admins.

No component_id: these are free-form needs, never capability grants. Admin
handling records a response; it must not write grants or claim fulfilment.
"""
import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from backend.app import create_app
from backend.dynamo_store import DynamoStore, DynamoUnit
from .conftest import ORIGIN, login

SUMMARY = "Access to the quarterly market data feed"
DETAILS = "Needed for research briefs; not available in the AI Catalog."


def submit(client, summary=SUMMARY, details=DETAILS, expect=201):
    response = client.post("/api/general-requests", json={"summary": summary, "details": details})
    assert response.status_code == expect, response.text
    return response.json()


def test_business_submits_and_lists_own_general_request(client):
    login(client)
    created = submit(client)
    assert created["id"]
    rows = client.get("/api/general-requests").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["summary"] == SUMMARY and row["details"] == DETAILS
    assert row["status"] == "SUBMITTED" and row["resolution"] is None
    assert row["requester"] == "alex" and row["workspace"] == "research"


def test_general_request_survives_process_restart(tmp_path):
    path = str(tmp_path / "restart.sqlite")
    with TestClient(create_app(path, demo_mode=True, worker_enabled=False), base_url=ORIGIN) as client:
        login(client)
        request_id = submit(client)["id"]
    with TestClient(create_app(path, demo_mode=True, worker_enabled=False), base_url=ORIGIN) as client:
        login(client)
        rows = client.get("/api/general-requests").json()
        assert [r["id"] for r in rows] == [request_id]
        assert rows[0]["status"] == "SUBMITTED"


@pytest.mark.parametrize("method,path", [("get", "/api/general-requests"), ("post", "/api/general-requests"), ("post", "/api/admin/general-requests/unknown/status")])
def test_unauthenticated_general_request_routes_rejected(client, method, path):
    assert getattr(client, method)(path, headers={"Origin": ORIGIN}).status_code == 401


def test_admin_cannot_submit_and_business_cannot_handle(client):
    login(client, "admin")
    assert client.post("/api/general-requests", json={"summary": SUMMARY, "details": ""}).status_code == 403
    login(client)
    request_id = submit(client)["id"]
    assert client.post(f"/api/admin/general-requests/{request_id}/status", json={"status": "RESOLVED", "note": "Handled offline"}).status_code == 403


@pytest.mark.parametrize("body,code", [
    ({"summary": "hey", "details": ""}, 422),                              # too short
    ({"summary": "  hey    ", "details": ""}, 422),                        # strips below minimum
    ({"summary": SUMMARY, "details": "", "component_id": "x"}, 422),       # no component field exists
])
def test_submission_validation(client, body, code):
    login(client)
    assert client.post("/api/general-requests", json=body).status_code == code


def test_workspace_scope_isolation_and_admin_visibility(client):
    login(client)
    request_id = submit(client)["id"]
    login(client, "sam")
    assert client.get("/api/general-requests").json() == []
    login(client, "admin")
    rows = client.get("/api/general-requests").json()
    assert [r["id"] for r in rows] == [request_id]


def test_admin_handles_request_without_granting(client):
    login(client)
    request_id = submit(client)["id"]
    login(client, "admin")
    grants_before = client.get("/api/admin/catalog").json()["grants"]
    assert client.post(f"/api/admin/general-requests/{request_id}/status", json={"status": "IN_PROGRESS", "note": "Scoping with the data team"}).status_code == 200
    assert client.post(f"/api/admin/general-requests/{request_id}/status", json={"status": "RESOLVED", "note": "Feed onboarding scheduled with the data team."}).status_code == 200
    # Terminal states are final; no silent re-open or re-resolution.
    assert client.post(f"/api/admin/general-requests/{request_id}/status", json={"status": "CLOSED", "note": "Trying to reopen"}).status_code == 409
    assert client.post("/api/admin/general-requests/unknown/status", json={"status": "CLOSED", "note": "Nothing here"}).status_code == 404
    assert client.post(f"/api/admin/general-requests/{request_id}/status", json={"status": "APPROVED", "note": "Wrong vocabulary"}).status_code == 422
    assert client.get("/api/admin/catalog").json()["grants"] == grants_before, "handling must never create a grant"
    login(client)
    row = client.get("/api/general-requests").json()[0]
    assert row["status"] == "RESOLVED"
    assert row["resolution"] == "Feed onboarding scheduled with the data team."


def test_duplicate_open_request_rejected_until_closed(client):
    login(client)
    request_id = submit(client)["id"]
    submit(client, expect=409)
    login(client, "admin")
    client.post(f"/api/admin/general-requests/{request_id}/status", json={"status": "CLOSED", "note": "Superseded by resolved feed work"})
    login(client)
    submit(client, expect=201)


def test_hourly_budget(client):
    login(client)
    for i in range(30):
        submit(client, summary=f"Need synthetic resource number {i} for research")
    submit(client, summary="One request over the hourly budget", expect=429)


def test_capability_request_history_remains_separate(client):
    login(client)
    response = client.post("/api/requests", json={"component_id": "restricted-insights", "reason": "Need synthetic strategy evidence."})
    assert response.status_code == 201, response.text
    capability_id = response.json()["id"]
    general_id = submit(client)["id"]
    capability_rows = client.get("/api/requests").json()
    assert [r["id"] for r in capability_rows] == [capability_id]
    assert capability_rows[0]["component"] == "restricted-insights"
    general_rows = client.get("/api/general-requests").json()
    assert [r["id"] for r in general_rows] == [general_id]
    assert "component" not in general_rows[0]


def test_audit_trail_records_lifecycle(client):
    login(client)
    request_id = submit(client)["id"]
    login(client, "admin")
    client.post(f"/api/admin/general-requests/{request_id}/status", json={"status": "RESOLVED", "note": "Provisioned through operations"})
    actions = {(row["action"], row["resource"]) for row in client.get("/api/admin/audit").json()}
    assert ("general_request_submitted", request_id) in actions
    assert ("general_request_updated", request_id) in actions


def test_dynamo_adapter_persists_general_requests():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-west-2")
        resource.create_table(TableName="synthetic-general-requests",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": k, "AttributeType": "S"} for k in ("pk", "sk")],
            BillingMode="PAY_PER_REQUEST")
        store = DynamoStore("synthetic-general-requests", resource)
        store.initialize()
        app = create_app(demo_mode=True, worker_enabled=False, repository=store)
        with TestClient(app, base_url=ORIGIN) as client:
            login(client)
            request_id = submit(client)["id"]
            login(client, "admin")
            assert client.post(f"/api/admin/general-requests/{request_id}/status", json={"status": "IN_PROGRESS", "note": "Scheduling with operations"}).status_code == 200
        # Durable rows readable by a fresh unit of work, not request-local state.
        unit = DynamoUnit(resource.Table("synthetic-general-requests"))
        rows = unit.select("general_requests").fetchall()
        assert len(rows) == 1 and rows[0]["id"] == request_id
        assert rows[0]["status"] == "IN_PROGRESS" and rows[0]["workspace"] == "research"
        # Pre-existing seeded entities remain intact (no destructive migration).
        assert unit.select("components").fetchall() and unit.select("settings", where=[("key", "=", "seeded")]).fetchone()
