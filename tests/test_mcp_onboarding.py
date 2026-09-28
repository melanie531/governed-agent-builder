"""Generic onboarding uses the actual HTTP, durable-job and catalog boundaries."""
import copy
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.foundation_runs import get, put
from backend.mcp_onboarding import McpOnboarding
from backend.store import Store
from tests.conftest import ORIGIN, login
from tests.journey_support import make_journey

CONFIG = {
    "enabled": True,
    "registry_id": "test-registry",
    "registry_arn": "arn:aws:agent-registry:us-west-2:123456789012:registry/test-registry",
    "workspaces": ["research", "operations"],
    "connections": [{
        "id": "data-service", "name": "Data service PAT",
        "allowed_origins": ["https://data.example.com"],
        "configuration": {"credentialProviderType": "API_KEY", "credentialProvider": {
            "apiKeyCredentialProvider": {
                "providerArn": "arn:aws:bedrock-agentcore:us-west-2:123456789012:token-vault/default/apikeycredentialprovider/data",
                "credentialParameterName": "Authorization", "credentialPrefix": "Bearer",
                "credentialLocation": "HEADER"}}},
    }],
}
BODY = {"name": "Data platform", "description": "Explore authorized datasets.",
        "endpoint": "https://data.example.com/mcp", "connection_id": "data-service",
        "workspaces": ["research"], "idempotency_key": "generic-onboard-00000001"}


class Cloud:
    def __init__(self):
        self.native, self.writes = {}, []
        self.lose_response = None
        self.tools = [{"name": "list_datasets", "description": "List available datasets.",
                       "inputSchema": {"type": "object", "properties": {}}}]

    def read(self, stage, state, config):
        return copy.deepcopy(self.native.get((state["id"], stage)))

    def write(self, stage, state, config):
        self.writes.append(stage)
        self.native[(state["id"], stage)] = {
            "connect": {"target_id": "target-1"},
            "register": {"record_id": "record-1", "record_arn": CONFIG["registry_arn"] + "/record/record-1"},
            "submit": {"status": "PENDING_APPROVAL"}, "approve": {"status": "APPROVED"},
        }[stage]
        if self.lose_response == stage:
            raise TimeoutError("Synthetic lost acknowledgement")

    def discover(self, state, config):
        return [{**copy.deepcopy(t), "name": state["target_name"] + "___" + t["name"]} for t in self.tools]

    def verify(self, state, config):
        assert self.read("approve", state, config)["status"] == "APPROVED"
        return self.discover(state, config)


@pytest.fixture
def setup(tmp_path):
    store = Store(str(tmp_path / "onboarding.sqlite"))
    journey, _ = make_journey(store)
    with store.tx() as db:
        db.execute("CREATE TABLE principals(id TEXT PRIMARY KEY, body TEXT, expires REAL)")
        put(db, "mcp-onboarding", copy.deepcopy(CONFIG))
    cloud = Cloud()
    service = McpOnboarding(store, journey.settings, cloud)
    admin_cloud = SimpleNamespace(record=lambda binding: {
        "recordArn": binding["arn"], "recordVersion": "1.0.0", "recordType": "MCP", "status": "APPROVED",
        "descriptors": {"mcpServer": {"data": json.dumps({"remotes": [{"type": "streamable-http", "url": BODY["endpoint"]}]}),
                                    "additionalData": {"tools": {"data": json.dumps({"tools": cloud.tools})}}}}})
    app = create_app(repository=store, demo_mode=True, worker_enabled=False,
                     journey=journey, mcp_onboarding=service, admin_cloud=admin_cloud)
    with TestClient(app, base_url=ORIGIN) as client:
        login(client, "admin")
        yield client, store, service, cloud


def start(setup, body=None):
    response = setup[0].post("/api/admin/mcp/onboarding", json=body or BODY)
    assert response.status_code == 202, response.text
    return response.json()


def drain(service, job):
    for _ in range(10):
        service.step(job)


def detail(setup, created):
    return setup[0].get("/api/admin/mcp/onboarding/" + created["id"]).json()


def approve(setup, created):
    state = detail(setup, created)
    return setup[0].post("/api/admin/mcp/onboarding/" + created["id"] + "/publish", json={
        "discovery_digest": state["discovery_digest"], "tools": ["list_datasets"],
        "idempotency_key": "generic-publish-00000001"})


def test_generic_endpoint_discovery_requires_review_before_native_approval_and_publication(setup):
    client, store, service, cloud = setup
    created = start(setup)
    assert cloud.writes == []
    drain(service, created["job_id"])
    state = detail(setup, created)
    assert state["phase"] == "REVIEW"
    assert cloud.writes == ["connect", "register"]
    assert state["tools"][0]["name"] == "list_datasets"
    with store.tx() as db:
        assert not db.select("components", where=[("id", "=", state["catalog_id"])]).fetchone()
    result = approve(setup, created)
    assert result.status_code == 202, result.text
    drain(service, result.json()["job_id"])
    state = detail(setup, created)
    assert state["phase"] == "READY"
    assert cloud.writes == ["connect", "register", "submit", "approve"]
    with store.tx() as db:
        item = json.loads(db.select("components", where=[("id", "=", state["catalog_id"])]).fetchone()["body"])
        assert item["registry"]["arn"].endswith("/record/record-1")
        assert item["binding"]["target_id"] == "target-1"
        assert item["default_tool_ids"]
        assert item["provider"] != "Snowflake"
    assert "credentialProvider" not in json.dumps(state)
    record_path = "/api/admin/platform/catalog/" + state["catalog_id"] + "/registry"
    assert client.get(record_path).json()["status"] == "APPROVED"
    cloud.tools[0]["description"] = "Changed native metadata"
    assert client.get(record_path).status_code == 409


@pytest.mark.parametrize("patch", [
    {"endpoint": "http://data.example.com/mcp"}, {"endpoint": "https://127.0.0.1/mcp"},
    {"endpoint": "https://data.example.com.evil.test/mcp"},
    {"endpoint": "https://user:secret@data.example.com/mcp"},
    {"endpoint": "https://data.example.com/mcp?token=secret"},
    {"connection_id": "unknown"}, {"workspaces": ["platform"]},
    {"workspaces": ["research", "research"]}, {"pat": "not-accepted"},
])
def test_invalid_or_unbound_inputs_do_not_enqueue(setup, patch):
    response = setup[0].post("/api/admin/mcp/onboarding", json={**BODY, **patch})
    assert response.status_code == 422
    assert not setup[3].writes
    with setup[1].tx() as db:
        assert not list(db.select("jobs"))


def test_business_user_cannot_onboard_or_approve(setup):
    login(setup[0])
    assert setup[0].get("/api/admin/mcp/onboarding-options").status_code == 403
    assert setup[0].post("/api/admin/mcp/onboarding", json=BODY).status_code == 403


def test_explicit_create_retry_preserves_native_identity_and_does_not_replay_on_delivery(setup):
    client, store, service, cloud = setup
    cloud.lose_response = "register"
    created = start(setup)
    drain(service, created["job_id"])
    # Simulate a request that was rejected before creating the native record.
    del cloud.native[(created["id"], "register")]
    assert detail(setup, created)["retry_available"] is True
    original = service.tx(lambda db: service.load(db, created["id"]))
    cloud.lose_response = None
    path = "/api/admin/mcp/onboarding/" + created["id"] + "/retry"
    retry = client.post(path, json={"job_id": created["job_id"]})
    assert retry.status_code == 202, retry.text
    assert client.post(path, json={"job_id": created["job_id"]}).json() == retry.json()
    drain(service, created["job_id"])  # stale delivery cannot consume the retry
    drain(service, retry.json()["job_id"])
    current = service.tx(lambda db: service.load(db, created["id"]))
    assert current["phase"] == "REVIEW"
    assert current["id"] == original["id"]
    assert current["target_name"] == original["target_name"]
    assert current["operations"]["register"]["dispatch_count"] == 2
    assert len(current["operations"]["register"]["retry_requests"]) == 1
    assert cloud.writes == ["connect", "register", "register"]
    assert client.post(path, json={"job_id": retry.json()["job_id"]}).status_code == 409


def test_explicit_create_retry_reconciles_an_existing_record_without_writing(setup):
    client, _, service, cloud = setup
    cloud.lose_response = "register"
    created = start(setup)
    drain(service, created["job_id"])
    result = client.post("/api/admin/mcp/onboarding/" + created["id"] + "/retry", json={"job_id": created["job_id"]})
    assert result.status_code == 202
    drain(service, result.json()["job_id"])
    assert detail(setup, created)["phase"] == "REVIEW"
    assert cloud.writes == ["connect", "register"]


def test_reconciliation_without_a_native_result_returns_to_explicit_recovery(setup):
    client, _, service, cloud = setup
    cloud.lose_response = "register"
    created = start(setup)
    drain(service, created["job_id"])
    del cloud.native[(created["id"], "register")]
    result = client.post("/api/admin/mcp/onboarding/" + created["id"] + "/reconcile")
    drain(service, result.json()["job_id"])
    state = detail(setup, created)
    assert state["phase"] == "NEEDS_RECONCILIATION"
    assert state["retry_available"] is True
    assert cloud.writes == ["connect", "register"]


def test_service_failure_retains_sanitized_diagnostics(setup):
    from botocore.exceptions import ClientError
    _, store, service, cloud = setup
    def denied(*args):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message":
            "private-tool-content cannot perform agent-registry:CreateRegistryRecord"},
            "ResponseMetadata": {"RequestId": "request-123"}}, "CreateRegistryRecord")
    cloud.write = denied
    created = start(setup)
    drain(service, created["job_id"])
    state = detail(setup, created)
    assert state["failure_code"] == "AccessDeniedException"
    assert state["failure_context"]["request_id"] == "request-123"
    assert "private-tool-content" not in json.dumps(state)
    with store.tx() as db:
        assert "private-tool-content" not in json.dumps([dict(r) for r in db.select("audit")])


def test_acknowledged_native_creation_waits_for_visibility_without_replay(setup):
    _, _, service, cloud = setup
    original_write = cloud.write
    def delayed(stage, state, config):
        original_write(stage, state, config)
        if stage == "connect":
            del cloud.native[(state["id"], stage)]
    cloud.write = delayed
    created = start(setup)
    drain(service, created["job_id"])
    assert detail(setup, created)["phase"] == "CONNECTING"
    assert cloud.writes == ["connect"]
    cloud.native[(created["id"], "connect")] = {"target_id": "target-1"}
    drain(service, created["job_id"])
    assert detail(setup, created)["phase"] == "REVIEW"
    assert cloud.writes == ["connect", "register"]


def test_non_idempotent_approval_is_never_available_for_native_retry(setup):
    client, _, service, cloud = setup
    created = start(setup)
    drain(service, created["job_id"])
    cloud.lose_response = "approve"
    approval = approve(setup, created).json()
    drain(service, approval["job_id"])
    assert detail(setup, created)["retry_available"] is False
    assert client.post("/api/admin/mcp/onboarding/" + created["id"] + "/retry", json={"job_id": approval["job_id"]}).status_code == 409


def test_retry_checks_current_job_configuration_and_admin_authority(setup):
    client, store, service, cloud = setup
    cloud.lose_response = "register"
    created = start(setup)
    drain(service, created["job_id"])
    path = "/api/admin/mcp/onboarding/" + created["id"] + "/retry"
    assert client.post(path, json={"job_id": "0" * 32}).status_code == 409
    with store.tx() as db:
        changed = copy.deepcopy(CONFIG)
        changed["connections"][0]["name"] = "Changed"
        put(db, "mcp-onboarding", changed)
    assert client.post(path, json={"job_id": created["job_id"]}).status_code == 409
    login(client)
    assert client.post(path, json={"job_id": created["job_id"]}).status_code == 403
    assert cloud.writes == ["connect", "register"]


def test_lost_retry_response_stops_without_another_automatic_write(setup):
    client, _, service, cloud = setup
    cloud.lose_response = "register"
    created = start(setup)
    drain(service, created["job_id"])
    del cloud.native[(created["id"], "register")]
    result = client.post("/api/admin/mcp/onboarding/" + created["id"] + "/retry", json={"job_id": created["job_id"]})
    drain(service, result.json()["job_id"])
    assert detail(setup, created)["phase"] == "NEEDS_RECONCILIATION"
    assert cloud.writes == ["connect", "register", "register"]
    operation = service.tx(lambda db: service.load(db, created["id"]))["operations"]["register"]
    assert operation["retry_requested"] is False


def test_options_expose_connection_labels_and_allowed_origins_without_credentials(setup):
    data = setup[0].get("/api/admin/mcp/onboarding-options").json()
    assert data["connections"][0]["name"] == "Data service PAT"
    assert data["connections"][0]["auth_type"] == "API_KEY"
    assert "providerArn" not in json.dumps(data)


def test_lost_creation_response_reuses_same_request(setup):
    first = start(setup)
    assert start(setup) == first
    assert setup[0].get("/api/admin/mcp/onboarding-requests/" + BODY["idempotency_key"]).json()["id"] == first["id"]


def test_changed_discovery_cannot_be_published(setup):
    created = start(setup)
    drain(setup[2], created["job_id"])
    setup[3].tools[0]["inputSchema"]["properties"]["new"] = {"type": "string"}
    result = approve(setup, created)
    drain(setup[2], result.json()["job_id"])
    state = detail(setup, created)
    assert state["phase"] != "READY"
    with setup[1].tx() as db:
        assert not db.select("components", where=[("id", "=", state["catalog_id"])]).fetchone()


def test_uncertain_gateway_creation_reconciles_without_replaying_write(setup):
    setup[3].lose_response = "connect"
    created = start(setup)
    drain(setup[2], created["job_id"])
    assert detail(setup, created)["phase"] == "NEEDS_RECONCILIATION"
    setup[3].lose_response = None
    response = setup[0].post("/api/admin/mcp/onboarding/" + created["id"] + "/reconcile")
    assert response.status_code == 202
    drain(setup[2], response.json()["job_id"])
    assert detail(setup, created)["phase"] == "REVIEW"
    assert setup[3].writes.count("connect") == 1


def test_hosted_worker_schedules_durable_onboarding_continuation(setup, monkeypatch):
    from backend import serverless
    created = start(setup)
    queue = []
    monkeypatch.setattr(serverless, "application", lambda **_: setup[0].app)
    monkeypatch.setattr(serverless.boto3, "client", lambda _: SimpleNamespace(send_message=lambda **kw: queue.append(kw)))
    monkeypatch.setenv("JOB_QUEUE_URL", "https://queue.example.test/jobs")
    event = {"Records": [{"messageId": "delivery-1", "body": json.dumps({"job_id": created["job_id"]})}]}
    assert serverless.worker_handler(event, SimpleNamespace(get_remaining_time_in_millis=lambda: 300000)) == {"batchItemFailures": []}
    assert len(queue) == 1
    assert json.loads(queue[0]["MessageBody"]) == {"job_id": created["job_id"]}


def test_publication_is_bounded_before_native_approval(setup):
    setup[3].tools = [{"name": "tool_" + str(n), "description": "Tool",
                       "inputSchema": {"type": "object", "properties": {}}} for n in range(21)]
    created = start(setup)
    drain(setup[2], created["job_id"])
    state = detail(setup, created)
    response = setup[0].post("/api/admin/mcp/onboarding/" + created["id"] + "/publish", json={
        "discovery_digest": state["discovery_digest"], "tools": [t["name"] for t in state["tools"]],
        "idempotency_key": "bounded-publish-00000001"})
    assert response.status_code == 422
    assert setup[3].writes == ["connect", "register"]
