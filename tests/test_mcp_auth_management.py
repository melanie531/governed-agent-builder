import json

from backend.foundation_runs import get
from tests.conftest import login
from tests.test_mcp_onboarding import setup, start, BODY as MCP_BODY
from tests.test_mcp_credentials import enable, BODY


def auth(setup):
    credentials, cloud = enable(setup)
    cloud.versions = {}
    def inspect(stage, native, operation):
        if stage == "rotate":
            return cloud.versions.get(native["id"]) == operation["version"]
        return native["id"] not in (cloud.providers if stage == "provider_delete" else cloud.secrets)
    def write(stage, native, operation, value=None):
        cloud.writes.append(stage)
        if stage == "rotate":
            assert value == "replacement-test-token"
            cloud.versions[native["id"]] = operation["version"]
        elif stage == "provider_delete":
            cloud.providers.pop(native["id"], None)
        else:
            cloud.secrets.pop(native["id"], None)
        if cloud.lose == stage:
            raise TimeoutError(value)
    cloud.management_read, cloud.management_write = inspect, write
    value = setup[0].post("/api/admin/mcp/credentials", json=BODY).json()
    return "/api/admin/mcp/auth-connections/" + value["connection_id"], credentials, cloud


def edit(revision=1, **extra):
    return {**BODY, "secret": "replacement-test-token", "name": "Updated credential",
            "expected_revision": revision, "idempotency_key": "edit-auth-request-0001", **extra}


def deletion(revision=1, name=BODY["name"]):
    return {"expected_revision": revision, "confirm_name": name, "idempotency_key": "delete-auth-request-001"}


def test_edit_unused_auth_rotates_secret_and_revises_metadata_without_disclosing_values(setup):
    path, _, cloud = auth(setup)
    response = setup[0].post(path + "/edit", json=edit())
    assert response.status_code == 200, response.text
    assert response.json()["phase"] == "READY"
    item = setup[0].get(path).json()
    assert item["revision"] == 2 and item["name"] == "Updated credential"
    assert setup[0].post(path + "/edit", json=edit()).json() == response.json()
    assert cloud.writes.count("rotate") == 1
    with setup[1].tx() as db:
        saved = json.dumps([dict(r) for r in db.select("settings")] + [dict(r) for r in db.select("audit")])
    assert "replacement-test-token" not in saved + response.text
    assert setup[0].post(path + "/delete", json=deletion()).status_code == 409


def test_delete_unused_auth_and_old_creation_replay_cannot_resurrect_it(setup):
    path, _, cloud = auth(setup)
    response = setup[0].post(path + "/delete", json=deletion())
    assert response.status_code == 200, response.text
    assert response.json()["phase"] == "DELETED"
    assert cloud.writes[-2:] == ["provider_delete", "secret_delete"]
    assert setup[0].post("/api/admin/mcp/credentials", json=BODY).json()["phase"] == "DELETED"
    assert setup[0].get("/api/admin/mcp/auth-connections").json()["items"][0]["id"] == "data-service"
    with setup[1].tx() as db:
        assert get(db, "mcp-auth:" + path.rsplit("/", 1)[1]) is None


def test_credential_reference_blocks_edit_and_delete_and_deployment_credentials_are_preserved(setup):
    path, _, cloud = auth(setup)
    start(setup, {**MCP_BODY, "endpoint": BODY["endpoint"], "connection_id": path.rsplit("/", 1)[1]})
    current = setup[0].get(path).json()
    assert current["references"][0]["name"] == MCP_BODY["name"] and not current["can_edit"]
    writes = list(cloud.writes)
    assert setup[0].post(path + "/edit", json=edit()).status_code == 409
    assert setup[0].post(path + "/delete", json=deletion()).status_code == 409
    assert setup[0].post("/api/admin/mcp/auth-connections/data-service/delete", json=deletion()).status_code == 409
    assert cloud.writes == writes


def test_uncertain_rotation_requires_get_reconciliation_without_rewrite(setup):
    path, _, cloud = auth(setup)
    cloud.lose = "rotate"
    result = setup[0].post(path + "/edit", json=edit()).json()
    assert result["phase"] == "NEEDS_RECONCILIATION"
    assert setup[0].get(path + "/operations/" + edit()["idempotency_key"]).json()["phase"] == "READY"
    assert cloud.writes.count("rotate") == 1


def test_auth_management_requires_admin_and_sanitizes_validation_errors(setup):
    path, _, cloud = auth(setup)
    response = setup[0].post(path + "/edit", json=edit(secret={"bad": BODY["secret"]}))
    assert response.status_code == 422 and BODY["secret"] not in response.text
    assert setup[0].post(path + "/delete", json=deletion(name="wrong")).status_code == 422
    login(setup[0])
    assert setup[0].get(path).status_code == 403
    assert setup[0].post(path + "/edit", json=edit()).status_code == 403
