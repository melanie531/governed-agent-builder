"""MCP creation exercises the real HTTP, repository and agent-admission boundary."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.catalog import PERSONAS
from backend.foundation_runs import get, put
from backend.mcp_servers import McpServers
from backend.store import Store
from tests.conftest import ORIGIN, login
from tests.journey_support import definition, make_journey


PROFILE = {
    "id": "snowflake", "name": "Snowflake",
    "host": "org-account.snowflakecomputing.com",
    "database": "DEMO", "schema": "PUBLIC", "warehouse": "DEMO_WH",
    "creator_role": "MCP_CREATOR", "reader_role": "MCP_READER",
    "provisioning_secret_arn": "arn:aws:secretsmanager:us-west-2:123456789012:secret:governed-agent-builder-serverless/snowflake-mcp-provisioner-Ab1234",
    "credential_provider_arn": "arn:aws:bedrock-agentcore:us-west-2:123456789012:token-vault/default/apikeycredentialprovider/snowflake",
    "workspaces": ["research", "operations"],
    "tools": [
        {"name": "query_sql", "title": "Query Snowflake data", "type": "SYSTEM_EXECUTE_SQL",
         "description": "Discover schemas and query permitted data.",
         "config": {"read_only": True, "query_timeout": 30, "warehouse": "DEMO_WH"}},
        {"name": "analyst", "title": "Cortex Analyst", "type": "CORTEX_ANALYST_MESSAGE",
         "identifier": "DEMO.PUBLIC.METRICS", "description": "Generate SQL; execute it using query_sql.",
         "requires": ["query_sql"]},
    ],
}
BODY = {"profile_id": "snowflake", "name": "Data explorer", "description": "Existing data discovery",
        "tools": ["query_sql"], "workspaces": ["research"], "idempotency_key": "test-create-mcp-00000001"}


class Cloud:
    def __init__(self):
        self.native, self.writes, self.reads = {}, [], []
        self.lose_response = None
        self.not_accepted = False
        self.ready = True

    def read(self, stage, state, profile):
        self.reads.append(stage)
        return copy.deepcopy(self.native.get((state["id"], stage)))

    def write(self, stage, state, profile):
        self.writes.append(stage)
        result = {"create": {"server_name": state["server_name"]},
                  "grant": {"granted": True},
                  "connect": {"target_id": "target-" + state["id"][:12]}}[stage]
        if not self.not_accepted:
            self.native[(state["id"], stage)] = result
        if self.lose_response == stage:
            raise TimeoutError("Synthetic uncertain native response")
        return result

    def discover(self, state, profile):
        if not self.ready:
            return None
        return [{"name": state["target_name"] + "___" + name, "description": "Native tool",
                 "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}}}
                for name in state["tool_ids"]]


@pytest.fixture
def setup(tmp_path):
    store = Store(str(tmp_path / "mcp.sqlite"))
    journey, _ = make_journey(store)
    with store.tx() as db:
        db.execute("CREATE TABLE principals(id TEXT PRIMARY KEY, body TEXT, expires REAL)")
        db.insert("principals", {"id": "alex", "body": json.dumps(PERSONAS["alex"]), "expires": 9999999999})
        put(db, "mcp-platform", {"enabled": True, "profiles": [copy.deepcopy(PROFILE)]})
    cloud = Cloud()
    service = McpServers(store, journey.settings, cloud)
    app = create_app(repository=store, demo_mode=True, worker_enabled=False, journey=journey, mcp_servers=service)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, store, service, cloud, journey


def create(setup, body=None):
    client, _, _, _, _ = setup
    login(client, "admin")
    response = client.post("/api/admin/mcp/servers", json=body or BODY)
    assert response.status_code == 202, response.text
    return response.json()


def drain(service, job_id):
    for _ in range(10):
        service.step(job_id)


def test_admin_creation_publishes_actual_tools_usable_by_agent(setup):
    client, store, service, cloud, journey = setup
    created = create(setup)
    assert cloud.writes == []  # Enqueue commits before external work.
    drain(service, created["job_id"])
    current = client.get("/api/admin/mcp/servers/" + created["id"]).json()
    assert current["phase"] == "READY"
    assert cloud.writes == ["create", "grant", "connect"]
    assert current["endpoint"].endswith("/mcp-servers/" + current["server_name"])
    assert current["server_name"].startswith("STUDIO_")
    agent = definition(journey, "research")
    with store.tx() as db:
        item = json.loads(db.select("components", where=[("id", "=", current["catalog_id"])]).fetchone()["body"])
        assert item["default_tool_ids"] and item["approved"]
        assert item["binding"]["target_id"] == current["gateway_target_id"]
        agent.update(mcp_servers=[item["id"]], tools=item["default_tool_ids"])
        agent["component_versions"] = {cid: "1" for cid in [agent["model_id"], *agent["mcp_servers"], *agent["tools"], *agent["skills"]]}
        _, resolved = journey.validate(db, PERSONAS["alex"], agent)
        tool = resolved[agent["tools"][0]]
        assert tool["binding"]["name"].endswith("___query_sql")
        assert tool["binding"]["target_id"] == item["binding"]["target_id"]
        assert get(db, "mcp-server:" + created["id"])["phase"] == "READY"
    assert "provisioning_secret" not in json.dumps(current)
    assert "credential_provider" not in json.dumps(current)


@pytest.mark.parametrize("path", ["/options", "/servers", "/servers/missing", "/requests/test-create-mcp-00000001"])
def test_business_cannot_manage_mcp_connections(setup, path):
    client, _, _, cloud, _ = setup
    login(client)
    assert client.get("/api/admin/mcp" + path).status_code == 403
    assert client.post("/api/admin/mcp/servers", json=BODY).status_code == 403
    assert cloud.writes == []


def test_options_only_expose_approved_profiles_not_credentials(setup):
    client, _, _, _, _ = setup
    login(client, "admin")
    data = client.get("/api/admin/mcp/options").json()
    assert data["enabled"]
    assert data["profiles"][0]["tool_choices"][1]["requires"] == ["query_sql"]
    assert data["workspaces"] == ["operations", "research"]
    text = json.dumps(data)
    assert "secret" not in text and "credential_provider" not in text and "MCP_CREATOR" not in text


@pytest.mark.parametrize("patch", [
    {"tools": ["unapproved"]}, {"tools": ["analyst"]}, {"tools": ["query_sql", "query_sql"]},
    {"workspaces": ["platform"]}, {"profile_id": "unapproved"}, {"name": " "},
    {"endpoint": "https://attacker.example"}, {"pat": "synthetic-never-accept"},
    {"statement": "DROP DATABASE DEMO"}, {"idempotency_key": "short"},
])
def test_invalid_inputs_never_enqueue_or_call_cloud(setup, patch):
    client, store, _, cloud, _ = setup
    login(client, "admin")
    assert client.post("/api/admin/mcp/servers", json={**BODY, **patch}).status_code == 422
    with store.tx() as db:
        assert not list(db.select("jobs"))
    assert not cloud.writes


def test_duplicate_create_and_lost_http_response_reuse_canonical_job(setup):
    client, store, _, cloud, _ = setup
    created = create(setup)
    assert client.post("/api/admin/mcp/servers", json=BODY).json() == created
    recovered = client.get("/api/admin/mcp/requests/" + BODY["idempotency_key"])
    assert recovered.status_code == 200
    assert recovered.json()["id"] == created["id"]
    assert client.post("/api/admin/mcp/servers", json={**BODY, "name": "Changed"}).status_code == 409
    with store.tx() as db:
        assert len(list(db.select("jobs"))) == 1
    assert not cloud.writes


@pytest.mark.parametrize("stage", ["create", "grant", "connect"])
def test_uncertain_native_response_never_replays_write(setup, stage):
    client, _, service, cloud, _ = setup
    cloud.lose_response = stage
    created = create(setup)
    drain(service, created["job_id"])
    current = client.get("/api/admin/mcp/servers/" + created["id"]).json()
    assert current["phase"] == "NEEDS_RECONCILIATION"
    assert cloud.writes.count(stage) == 1
    # Explicit reconciliation consumes the existing resource, then continues.
    cloud.lose_response = None
    resumed = client.post("/api/admin/mcp/servers/" + created["id"] + "/reconcile", json={})
    assert resumed.status_code == 202
    assert resumed.json()["job_id"] != created["job_id"]
    # A new jobs INSERT is required by the real DynamoDB stream dispatcher.
    with service.store.tx() as db:
        assert db.select("jobs", where=[("id", "=", created["job_id"])]).fetchone()["stage"] == "UNKNOWN"
        assert db.select("jobs", where=[("id", "=", resumed.json()["job_id"])]).fetchone()
    previous_writes = list(cloud.writes)
    drain(service, created["job_id"])  # Late deliveries of the old job are inert.
    assert cloud.writes == previous_writes
    drain(service, resumed.json()["job_id"])
    assert client.get("/api/admin/mcp/servers/" + created["id"]).json()["phase"] == "READY"
    assert cloud.writes == ["create", "grant", "connect"]


def test_unaccepted_uncertain_write_stays_uncertain_without_retry(setup):
    client, _, service, cloud, _ = setup
    cloud.lose_response, cloud.not_accepted = "create", True
    created = create(setup)
    drain(service, created["job_id"])
    resumed = client.post("/api/admin/mcp/servers/" + created["id"] + "/reconcile", json={}).json()
    drain(service, resumed["job_id"])
    assert cloud.writes == ["create"]
    assert client.get("/api/admin/mcp/servers/" + created["id"]).json()["phase"] == "NEEDS_RECONCILIATION"


def test_existing_native_resource_is_not_adopted(setup):
    client, _, service, cloud, _ = setup
    created = create(setup)
    cloud.native[(created["id"], "create")] = {"server_name": "unrelated"}
    drain(service, created["job_id"])
    assert cloud.writes == []
    assert client.get("/api/admin/mcp/servers/" + created["id"]).json()["phase"] == "FAILED"


def test_profile_drift_stops_pending_creation(setup):
    client, store, service, cloud, _ = setup
    created = create(setup)
    with store.tx() as db:
        settings = get(db, "mcp-platform")
        settings["profiles"][0]["warehouse"] = "OTHER_WH"
        put(db, "mcp-platform", settings)
    drain(service, created["job_id"])
    assert not cloud.writes
    assert client.get("/api/admin/mcp/servers/" + created["id"]).json()["phase"] == "FAILED"


def test_target_not_ready_does_not_publish_and_does_not_repeat_creation(setup):
    client, store, service, cloud, _ = setup
    cloud.ready = False
    created = create(setup)
    drain(service, created["job_id"])
    current = client.get("/api/admin/mcp/servers/" + created["id"]).json()
    assert current["phase"] == "VERIFYING"
    with store.tx() as db:
        assert not db.select("components", where=[("id", "=", current["catalog_id"])]).fetchone()
    cloud.ready = True
    drain(service, created["job_id"])
    assert client.get("/api/admin/mcp/servers/" + created["id"]).json()["phase"] == "READY"
    assert cloud.writes == ["create", "grant", "connect"]


def test_csrf_failure_never_enqueues_creation(setup):
    client, store, _, cloud, _ = setup
    login(client, "admin")
    client.headers.pop("x-csrf-token", None)
    response = client.post("/api/admin/mcp/servers", json=BODY, headers={"Origin": "https://untrusted.example"})
    assert response.status_code == 403
    with store.tx() as db:
        assert not list(db.select("jobs"))
    assert not cloud.writes


def test_active_lease_prevents_duplicate_dispatch(setup):
    _, store, service, cloud, _ = setup
    created = create(setup)
    with store.tx() as db:
        state = get(db, "mcp-server:" + created["id"])
        state["claim"] = {"token": "other-worker", "expires": 9999999999}
        put(db, "mcp-server:" + created["id"], state)
    service.step(created["job_id"])
    assert not cloud.reads and not cloud.writes


def test_discovery_mismatch_never_publishes(setup):
    client, store, service, cloud, _ = setup
    cloud.discover = lambda *_: []
    created = create(setup)
    drain(service, created["job_id"])
    assert client.get("/api/admin/mcp/servers/" + created["id"]).json()["phase"] == "NEEDS_RECONCILIATION"
    with store.tx() as db:
        assert not db.select("components", where=[("id", "=", "mcp-studio-" + created["id"])]).fetchone()


def test_hosted_job_without_bound_authority_stops_before_native_work(setup):
    client, _, service, cloud, _ = setup
    created = create(setup)
    service.hosted = True
    drain(service, created["job_id"])
    assert client.get("/api/admin/mcp/servers/" + created["id"]).json()["phase"] == "FAILED"
    assert not cloud.writes
