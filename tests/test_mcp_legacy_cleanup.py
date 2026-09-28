"""Retiring remote provisioning must preserve saved connections and native receipts."""
import copy
import json
import time

import pytest

from backend.foundation_runs import get, put
from tests.conftest import login
from tests.test_mcp_onboarding import CONFIG, setup


def legacy_record(store, *, phase="READY"):
    now = time.time()
    state = {
        "id": "legacy-server", "job_id": "legacy-job", "profile_id": "old-provider",
        "name": "Existing data connection", "description": "Customer-owned endpoint",
        "endpoint": "https://data.example.com/mcp", "target_name": "old-target",
        "gateway_target_id": "old-target-id", "catalog_id": "old-catalog",
        "tool_ids": ["query_sql"], "workspaces": ["research"], "phase": phase,
        "owner": "admin", "requester": "admin", "created": now, "updated": now,
        "deadline": now + 900, "claim": None,
        "operations": {"create": {"intent": True, "status": "DISPATCHING",
                                  "request_id": "retained-native-request"}},
    }
    provider = CONFIG["connections"][0]["configuration"]["credentialProvider"]["apiKeyCredentialProvider"]["providerArn"]
    with store.tx() as db:
        put(db, "mcp-platform", {"enabled": True, "profiles": [
            {"id": "old-provider", "credential_provider_arn": provider}]})
        put(db, "mcp-server:" + state["id"], state)
    return state


def test_remote_provisioning_routes_are_unavailable(setup):
    client, store, _, cloud = setup
    body = {"profile_id": "old-provider", "name": "Must not provision",
            "tools": ["query_sql"], "workspaces": ["research"],
            "idempotency_key": "retired-create-request-001"}
    assert client.post("/api/admin/mcp/servers", json=body).status_code == 405
    assert client.get("/api/admin/mcp/options").status_code == 404
    assert client.post("/api/admin/mcp/servers/old/reconcile", json={}).status_code in (404, 405)
    with store.tx() as db:
        assert not db.select("jobs").fetchall()
    assert cloud.writes == []


def test_legacy_read_and_dependency_protection_do_not_need_provisioning_profiles(setup):
    client, store, _, cloud = setup
    state = legacy_record(store)
    with store.tx() as db:
        db.insert("agents", {"id": "saved-agent", "owner": "owner", "workspace": "research",
                            "current_version": 1, "created": time.time()})
        db.insert("versions", {"agent": "saved-agent", "version": 1, "digest": "saved",
                              "created": time.time(), "body": json.dumps({
                                  "name": "Saved agent", "mcp_servers": [state["catalog_id"]]})})
        version = dict(db.select("versions").fetchone())
    assert client.get("/api/admin/mcp/servers").json()["items"][0]["id"] == state["id"]
    info = client.get("/api/admin/mcp/servers/" + state["id"]).json()
    assert info["gateway_target_id"] == state["gateway_target_id"]
    assert "operations" not in info and "credential_provider_arn" not in json.dumps(info)
    path = "/api/admin/mcp/management/servers/" + state["id"]
    info = client.get(path).json()
    assert info["blockers"][0]["id"] == "saved-agent"
    assert not info["can_edit"] and not info["can_delete"]
    assert client.post(path + "/delete", json={
        "expected_revision": 1, "confirm_name": state["name"],
        "idempotency_key": "protected-legacy-delete-001"}).status_code == 409
    with store.tx() as db:
        assert get(db, "mcp-server:" + state["id"]) == state
        assert dict(db.select("versions").fetchone()) == version
    assert cloud.writes == []
    login(client)
    assert client.get("/api/admin/mcp/servers").status_code == 403
    assert client.get("/api/admin/mcp/servers/" + state["id"]).status_code == 403


@pytest.mark.parametrize("stage", ["CONNECTING", "SUCCEEDED", "UNKNOWN"])
def test_retired_jobs_do_not_replay_native_work_or_rewrite_history(setup, stage):
    client, store, _, cloud = setup
    state = legacy_record(store, phase="CONNECTING" if stage != "SUCCEEDED" else "READY")
    with store.tx() as db:
        put(db, "mcp-job:" + state["job_id"], {"server_id": state["id"]})
        db.insert("jobs", {"id": state["job_id"], "agent": "mcp:" + state["id"], "version": 1,
                          "requester": "admin", "idem": state["job_id"], "stage": stage,
                          "result": "{}", "created": state["created"], "updated": state["updated"],
                          "deadline": state["deadline"], "attempts": 0})
        before = dict(db.select("jobs").fetchone())
    client.app.state.step_job(state["job_id"])
    client.app.state.step_job(state["job_id"])
    with store.tx() as db:
        job = dict(db.select("jobs").fetchone())
        assert get(db, "mcp-server:" + state["id"]) == state
        if stage == "CONNECTING":
            assert job["stage"] == "UNKNOWN"
            assert json.loads(job["result"])["code"] == "REMOTE_MCP_PROVISIONING_RETIRED"
        else:
            assert job == before
    assert cloud.writes == []
    response = client.get("/api/admin/mcp/servers/" + state["id"]).json()
    assert response["phase"] == ("READY" if stage == "SUCCEEDED" else "NEEDS_RECONCILIATION")


def test_old_provisioning_settings_do_not_grant_creator_secret_access(setup):
    from infra.serverless import template
    settings = {**copy.deepcopy(setup[2].settings), "mcp_onboarding": copy.deepcopy(CONFIG),
                "evaluator_id": "Builtin.Correctness",
                "evaluator_arn": "arn:aws:bedrock-agentcore:::evaluator/Builtin.Correctness",
                "mcp_creation": {
                    "provisioning_secret_arns": [
                        "arn:aws:secretsmanager:us-west-2:123456789012:secret:governed-agent-builder-serverless/retired-creator-Ab1234"],
                    "reader_secret_arns": [],
                    "credential_provider_arns": [
                        CONFIG["connections"][0]["configuration"]["credentialProvider"]["apiKeyCredentialProvider"]["providerArn"]]}}
    body = template(journey=settings)
    assert "retired-creator" not in json.dumps(body)
    assert "McpCreation" not in json.dumps(body)
    assert "McpOnboarding" in json.dumps(body)
    assert body["Resources"]["RoleSwitchers"]["Properties"]["GroupName"] == "studio-role-switcher"
    assert body["Resources"]["RoleSwitchers"]["Properties"]["Description"] == (
        "Explicitly enrolled users may switch between their assigned business and admin roles")
