import copy
import json
import time

import pytest

from backend.foundation_runs import get, put
from tests.test_mcp_onboarding import setup
from tests.test_mcp_python import BODY, CONFIG, create, drain, enable


def deletion(setup, value, **changes):
    return setup[0].post("/api/admin/mcp/deployments/" + value["id"] + "/delete", json={
        "confirm_name": BODY["name"], "expected_revision": 1,
        "idempotency_key": "delete-mcp-deployment-0001", **changes})


def deletion_cloud(cloud):
    cloud.retired = []
    cloud.resources = [{"kind": "runtime", "id": "owned-runtime"},
                       {"kind": "identity", "name": "owned-runtime"},
                       {"kind": "object", "key": "owned/runtime.zip", "version_id": "v1"}]
    cloud.retirement_plan = lambda state, config: copy.deepcopy(cloud.resources)
    cloud.retirement_read = lambda resource, state, config: {"absent": resource in cloud.retired, "pending": False}
    cloud.retirement_write = lambda resource, state, config: cloud.retired.append(copy.deepcopy(resource))
    return cloud


def test_delete_is_confirmed_idempotent_and_frees_deployment_slots(setup):
    _, cloud = enable(setup)
    deletion_cloud(cloud)
    value = create(setup)
    drain(setup, value["job_id"])
    listing = setup[0].get("/api/admin/mcp/deployments")
    assert listing.status_code == 200, listing.text
    assert listing.json()["items"][0]["id"] == value["id"]
    path = "/api/admin/mcp/deployments/" + value["id"]
    info = setup[0].get(path).json()
    assert info["can_delete"] and info["revision"] == 1
    assert deletion(setup, value, confirm_name="wrong").status_code == 422
    assert deletion(setup, value, expected_revision=2).status_code == 409
    assert not cloud.retired
    result = deletion(setup, value)
    assert result.status_code == 202, result.text
    assert result.json()["phase"] == "DELETING"
    assert deletion(setup, value).json()["job_id"] == result.json()["job_id"]
    for _ in range(12):
        setup[0].app.state.step_job(result.json()["job_id"])
    assert cloud.retired == cloud.resources
    assert setup[0].get(path).json()["phase"] == "DELETED"
    assert not setup[0].get("/api/admin/mcp/deployments").json()["items"]
    assert not setup[0].get("/api/admin/mcp/python").json()["items"]
    assert setup[0].post("/api/admin/mcp/python/" + value["id"] + "/reconcile", json={}).status_code == 409
    # Replaying creation must return its tombstone, never recreate the Runtime.
    assert create(setup)["phase"] == "DELETED"
    assert cloud.writes == ["package", "runtime", "logs"]


@pytest.mark.parametrize("native_endpoint", [False, True])
def test_deployment_deletion_blocks_registered_endpoints_and_saved_agents(setup, native_endpoint):
    _, cloud = enable(setup)
    deletion_cloud(cloud)
    value = create(setup)
    drain(setup, value["job_id"])
    with setup[1].tx() as db:
        state = get(db, "mcp-python:" + value["id"])
        endpoint = state["endpoint"]
        if native_endpoint:
            from backend.mcp_deployments import endpoints
            endpoint = next(e for e in endpoints(state) if "/invocations" in e)
        put(db, "mcp-connection:used", {"id": "used", "name": "Published tools",
            "phase": "REVIEW", "endpoint": endpoint, "catalog_id": "used-catalog"})
    path = "/api/admin/mcp/deployments/" + value["id"]
    info = setup[0].get(path).json()
    assert not info["can_delete"]
    assert info["blockers"][0]["name"] == "Published tools"
    assert deletion(setup, value).status_code == 409
    assert not cloud.retired


@pytest.mark.parametrize("persisted", [True, False])
def test_delete_response_loss_requires_read_reconciliation_and_explicit_retry(setup, persisted):
    _, cloud = enable(setup)
    deletion_cloud(cloud)
    value = create(setup)
    drain(setup, value["job_id"])
    calls = []
    original = cloud.retirement_write
    def uncertain(resource, state, config):
        calls.append(resource)
        if persisted:
            original(resource, state, config)
        raise TimeoutError("private-cloud-detail")
    cloud.retirement_write = uncertain
    result = deletion(setup, value)
    assert result.status_code == 202, result.text
    drain(setup, result.json()["job_id"])
    path = "/api/admin/mcp/deployments/" + value["id"]
    current = setup[0].get(path).json()
    assert current["phase"] == "NEEDS_RECONCILIATION"
    assert "private-cloud-detail" not in json.dumps(current)
    assert len(calls) == 1
    cloud.retirement_write = original
    job = setup[0].post(path + "/reconcile", json={})
    assert job.status_code == 202
    drain(setup, job.json()["job_id"])
    if not persisted:
        current = setup[0].get(path).json()
        assert current["phase"] == "NEEDS_RECONCILIATION" and current["retry_available"]
        assert not cloud.retired
        retry = setup[0].post(path + "/retry", json={"job_id": current["job_id"]})
        assert retry.status_code == 202
        drain(setup, retry.json()["job_id"])
    assert setup[0].get(path).json()["phase"] == "DELETED"
    assert cloud.retired == cloud.resources


def test_business_user_cannot_delete_a_deployment(setup):
    from tests.conftest import login
    enable(setup)
    value = create(setup)
    login(setup[0])
    assert setup[0].get("/api/admin/mcp/deployments").status_code == 403
    assert deletion(setup, value).status_code == 403


def test_saved_agent_reference_blocks_deletion_without_a_registration(setup):
    _, cloud = enable(setup)
    deletion_cloud(cloud)
    value = create(setup)
    drain(setup, value["job_id"])
    with setup[1].tx() as db:
        db.insert("agents", {"id": "agent-1", "owner": "owner", "workspace": "research",
                            "current_version": 1, "created": time.time()})
        db.insert("versions", {"agent": "agent-1", "version": 1, "digest": "d", "created": time.time(),
                              "body": json.dumps({"name": "Working agent", "mcp_deployment": value["id"]})})
    info = setup[0].get("/api/admin/mcp/deployments/" + value["id"]).json()
    assert info["blockers"][0]["name"] == "Working agent" and not info["can_delete"]
    assert deletion(setup, value).status_code == 409
    assert not cloud.retired


def test_deletion_fences_new_connections_immediately_and_deleted_receipts_do_not_use_quota(setup):
    _, cloud = enable(setup)
    deletion_cloud(cloud)
    value = create(setup)
    drain(setup, value["job_id"])
    endpoint = setup[0].get("/api/admin/mcp/python/" + value["id"]).json()["endpoint"]
    result = deletion(setup, value).json()
    response = setup[0].post("/api/admin/mcp/onboarding", json={
        "name": "Cannot resurrect", "endpoint": endpoint, "connection_id": "unknown",
        "workspaces": ["research"], "idempotency_key": "cannot-revive-runtime-0001"})
    assert response.status_code == 409
    assert "deleted" in response.json()["detail"]
    drain(setup, result["job_id"])
    with setup[1].tx() as db:
        for i in range(19):
            put(db, "mcp-python:active-" + str(i), {"id": "active-" + str(i), "phase": "READY"})
    assert create(setup, {**BODY, "idempotency_key": "after-deletion-slot-0001"})["phase"] == "PACKAGING"
    assert setup[0].post("/api/admin/mcp/python", json={
        **BODY, "idempotency_key": "after-deletion-full-0001"}).status_code == 429


def worker_cleanup(setup, monkeypatch):
    from types import SimpleNamespace
    from backend import serverless

    _, cloud = enable(setup)
    deletion_cloud(cloud)
    value = create(setup)
    drain(setup, value["job_id"])
    accepted = deletion(setup, value).json()
    messages = []
    monkeypatch.setattr(serverless, "application", lambda **_: setup[0].app)
    monkeypatch.setattr(serverless.boto3, "client",
        lambda _: SimpleNamespace(send_message=lambda **kw: messages.append(kw)))
    monkeypatch.setenv("JOB_QUEUE_URL", "synthetic-mcp-cleanup-queue")
    event = {"Records": [{"messageId": "mcp-cleanup",
                         "body": json.dumps({"job_id": accepted["job_id"]})}]}
    return cloud, accepted, event, messages


def test_maximum_package_cleanup_finishes_below_native_recursion_limit(setup, monkeypatch):
    from types import SimpleNamespace
    from backend import serverless
    from backend.mcp_package import MAX_ZIP, PART_BYTES

    cloud, accepted, event, messages = worker_cleanup(setup, monkeypatch)
    cloud.resources.extend({"kind": "object", "key": f"owned/part-{n:04d}", "version_id": "v1"}
                           for n in range(MAX_ZIP // PART_BYTES))
    context = SimpleNamespace(get_remaining_time_in_millis=lambda: 300000)
    deliveries = 0
    path = "/api/admin/mcp/deployments/" + accepted["id"]
    while setup[0].get(path).json()["phase"] != "DELETED":
        before = len(messages)
        assert serverless.worker_handler(event, context) == {"batchItemFailures": []}
        deliveries += 1
        assert deliveries < 16, "Lambda drops recursively queued invocations at the native limit"
        if setup[0].get(path).json()["phase"] != "DELETED":
            assert len(messages) == before + 1
            assert json.loads(messages[-1]["MessageBody"]) == {"job_id": accepted["job_id"]}
    assert len(cloud.retired) == len(cloud.resources) == 35
    assert deliveries <= 4
    before = len(messages)
    assert serverless.worker_handler(event, context) == {"batchItemFailures": []}
    assert len(messages) == before and cloud.retired == cloud.resources


@pytest.mark.parametrize("reason", ["time", "claim", "pending"])
def test_mcp_deletion_batch_yields_when_no_safe_progress_is_available(setup, monkeypatch, reason):
    from types import SimpleNamespace
    from backend import serverless

    cloud, accepted, event, messages = worker_cleanup(setup, monkeypatch)
    setup[0].app.state.step_job(accepted["job_id"])  # Freeze the inventory.
    if reason == "claim":
        with setup[1].tx() as db:
            state = get(db, "mcp-python:" + accepted["id"])
            state["claim"] = {"token": "other-worker", "expires": time.time() + 330}
            put(db, "mcp-python:" + accepted["id"], state)
    if reason == "pending":
        cloud.retirement_read = lambda *args: {"absent": False, "pending": True}
    context = SimpleNamespace(get_remaining_time_in_millis=lambda: 240000 if reason == "time" else 300000)
    assert serverless.worker_handler(event, context) == {"batchItemFailures": []}
    assert len(messages) == 1 and messages[0]["DelaySeconds"] == 10
    assert len(cloud.retired) == (1 if reason == "time" else 0)
