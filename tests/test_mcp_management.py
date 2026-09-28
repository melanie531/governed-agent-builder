import copy
import json
import time

from backend.foundation_runs import get, put
from tests.conftest import login
from tests.test_mcp_onboarding import BODY, CONFIG, setup, start, drain, detail, approve


def ready(setup):
    created = start(setup)
    drain(setup[2], created["job_id"])
    published = approve(setup, created).json()
    drain(setup[2], published["job_id"])
    return detail(setup, created)


def deletion_cloud(cloud):
    read, write = cloud.read, cloud.write
    def inspect(stage, state, config):
        if stage.startswith("retire_"):
            native_stage = {"retire_registry": "register", "retire_target": "connect"}[stage]
            return {"absent": (state["id"], native_stage) not in cloud.native, "pending": False}
        return read(stage, state, config)
    def mutate(stage, state, config):
        if stage.startswith("retire_"):
            cloud.writes.append(stage)
            native_stage = {"retire_registry": "register", "retire_target": "connect"}[stage]
            cloud.native.pop((state["id"], native_stage), None)
            if native_stage == "register":
                for old in ("submit", "approve"):
                    cloud.native.pop((state["id"], old), None)
            if cloud.lose_response == stage:
                raise TimeoutError("Lost delete acknowledgement")
            return
        return write(stage, state, config)
    cloud.read, cloud.write = inspect, mutate


def path(state):
    return "/api/admin/mcp/management/onboarding/" + state["id"]


def delete_body(state):
    return {"expected_revision": state.get("revision", 1), "confirm_name": state["name"],
            "idempotency_key": "delete-connection-request-001"}


def test_delete_removes_owned_bindings_and_catalog_but_retains_history_and_credentials(setup):
    client, store, service, cloud = setup
    deletion_cloud(cloud)
    state = ready(setup)
    original_config = service.tx(service.config)
    response = client.post(path(state) + "/delete", json=delete_body(state))
    assert response.status_code == 202, response.text
    assert client.post(path(state) + "/delete", json=delete_body(state)).json() == response.json()
    drain(service, response.json()["job_id"])
    assert detail(setup, state)["phase"] == "DELETED"
    assert client.get("/api/admin/mcp/onboarding").json()["items"] == []
    assert cloud.writes[-2:] == ["retire_registry", "retire_target"]
    assert service.tx(service.config) == original_config
    with store.tx() as db:
        assert not db.select("components", where=[("id", "=", state["catalog_id"])]).fetchone()
        assert get(db, "mcp-connection:" + state["id"])["history"]
    # The old create request cannot recreate a deleted registration.
    assert client.post("/api/admin/mcp/onboarding", json=BODY).json()["id"] == state["id"]
    assert detail(setup, state)["phase"] == "DELETED"


def test_edit_rediscovery_preserves_logical_identity_and_requires_new_publication(setup):
    client, store, service, cloud = setup
    deletion_cloud(cloud)
    state = ready(setup)
    old = service.tx(lambda db: service.load(db, state["id"]))
    body = {**BODY, "name": "Updated data", "endpoint": "https://data.example.com/updated",
            "expected_revision": 1, "idempotency_key": "edit-connection-request-001"}
    response = client.post(path(state) + "/edit", json=body)
    assert response.status_code == 202, response.text
    drain(service, response.json()["job_id"])
    updated = detail(setup, state)
    assert updated["id"] == state["id"] and updated["revision"] == 2
    assert updated["endpoint"] == body["endpoint"] and updated["phase"] == "REVIEW"
    new = service.tx(lambda db: service.load(db, state["id"]))
    assert new["target_name"] != old["target_name"]
    assert new["history"][0]["snapshot"]["endpoint"] == old["endpoint"]
    with store.tx() as db:
        assert not db.select("components", where=[("id", "=", state["catalog_id"])]).fetchone()
    publication = approve(setup, state).json()
    drain(service, publication["job_id"])
    assert detail(setup, state)["phase"] == "READY"
    assert client.post(path(state) + "/delete", json=delete_body(state)).status_code == 409


def test_management_reports_agent_dependencies_and_blocks_mutation_before_native_work(setup):
    client, store, service, cloud = setup
    state = ready(setup)
    with store.tx() as db:
        db.insert("agents", {"id": "agent-1", "owner": "owner", "workspace": "research",
                            "current_version": 1, "created": time.time()})
        db.insert("versions", {"agent": "agent-1", "version": 1, "digest": "d", "created": time.time(),
                              "body": json.dumps({"name": "Working agent", "mcp_servers": [state["catalog_id"]]})})
    before = list(cloud.writes)
    info = client.get(path(state)).json()
    assert info["blockers"][0]["name"] == "Working agent"
    assert info["can_edit"] is False and info["can_delete"] is False
    assert client.post(path(state) + "/delete", json=delete_body(state)).status_code == 409
    assert client.post(path(state) + "/edit", json={**BODY, "expected_revision": 1}).status_code == 409
    assert cloud.writes == before


def test_lost_native_delete_ack_is_reconciled_without_repeating_delete(setup):
    client, _, service, cloud = setup
    deletion_cloud(cloud)
    state = ready(setup)
    cloud.lose_response = "retire_registry"
    response = client.post(path(state) + "/delete", json=delete_body(state)).json()
    drain(service, response["job_id"])
    assert detail(setup, state)["phase"] == "NEEDS_RECONCILIATION"
    retry = client.post("/api/admin/mcp/onboarding/" + state["id"] + "/reconcile").json()
    drain(service, retry["job_id"])
    assert detail(setup, state)["phase"] == "DELETED"
    assert cloud.writes.count("retire_registry") == 1


def test_management_requires_admin_exact_confirmation_and_valid_origin(setup):
    client, _, _, cloud = setup
    state = ready(setup)
    before = list(cloud.writes)
    assert client.post(path(state) + "/delete", json={**delete_body(state), "confirm_name": "wrong"}).status_code == 422
    assert client.post(path(state) + "/edit", json={
        **BODY, "expected_revision": 1, "endpoint": "https://other.example.com/mcp"}).status_code == 422
    login(client)
    assert client.get(path(state)).status_code == 403
    assert client.post(path(state) + "/delete", json=delete_body(state)).status_code == 403
    assert cloud.writes == before


def test_legacy_registration_is_adopted_without_creating_or_dropping_remote_objects(setup):
    from tests.test_mcp_servers import PROFILE
    client, store, service, cloud = setup
    deletion_cloud(cloud)
    state = ready(setup)
    with store.tx() as db:
        legacy = service.load(db, state["id"])
        legacy.update(profile_id="snowflake")
        profile = {**PROFILE, "credential_provider_arn":
                   CONFIG["connections"][0]["configuration"]["credentialProvider"]["apiKeyCredentialProvider"]["providerArn"]}
        put(db, "mcp-platform", {"enabled": True, "profiles": [profile]})
        put(db, "mcp-server:" + state["id"], legacy)
        db.delete("settings", where=[("key", "=", "mcp-connection:" + state["id"])])
    response = client.post("/api/admin/mcp/management/servers/" + state["id"] + "/delete", json=delete_body(state))
    assert response.status_code == 202, response.text
    drain(service, response.json()["job_id"])
    assert detail(setup, state)["phase"] == "DELETED"
    with store.tx() as db:
        assert get(db, "mcp-server:" + state["id"])["management_adopted"] is True
    assert set(cloud.writes) <= {"connect", "register", "submit", "approve", "retire_registry", "retire_target"}
