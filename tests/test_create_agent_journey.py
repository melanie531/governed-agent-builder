import json
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.app import create_app
from backend.catalog import PERSONAS
from backend.foundation_runs import get, put
from backend.journey_catalog import SkillPublication, publish_skill
from backend.journey_schema import AgentDefinition, InvokeAgent, SaveAgent, VersionAction
from backend.live_catalog import grant_scope
from backend.store import Store
from tests.journey_support import definition, drain, make_journey
from backend import journey_lifecycle as lifecycle
from backend.journey_schema import DeleteAgent, DeletePreview


@pytest.fixture
def setup(tmp_path):
    store = Store(str(tmp_path / "state.sqlite"))
    return make_journey(store)


def save(journey, template="knowledge", dataset=None, key=None, deploy=True):
    return journey.save(PERSONAS["alex"], SaveAgent(definition=AgentDefinition(**definition(journey, template, dataset)),
                                                   idempotency_key=key or uuid4().hex, deploy=deploy))


@pytest.mark.parametrize("template", ["research", "knowledge"])
def test_no_dataset_deploys_and_invokes_without_any_evaluation(setup, template):
    journey, cloud = setup
    saved = save(journey, template)
    drain(journey)
    detail = journey.detail(PERSONAS["alex"], saved["agent_id"])
    assert detail["deployment"]["status"] == "DEPLOYED"
    assert detail["evaluation"]["status"] == "SKIPPED"
    assert detail["evaluation"]["score"] is None
    assert not cloud.evaluations
    job = journey.action(PERSONAS["alex"], saved["agent_id"],
                         InvokeAgent(version=1, idempotency_key=uuid4().hex, input="What is the support target?"), "invoke")
    drain(journey)
    assert journey.result(PERSONAS["alex"], job["job_id"])["phase"] == "SUCCEEDED"
    assert len(cloud.created) == 1
    assert len(cloud.invocations) == 2  # health check and explicit invocation


@pytest.mark.parametrize("template", ["research", "knowledge"])
def test_synthetic_cases_use_independent_sessions_and_evaluation_jobs(setup, template):
    journey, cloud = setup
    cases = next(t["sample_dataset"] for t in journey.options(PERSONAS["alex"])["templates"] if t["id"] == template)
    saved = save(journey, template, cases)
    drain(journey)
    result = journey.detail(PERSONAS["alex"], saved["agent_id"])
    assert result["deployment"]["status"] == "DEPLOYED"
    assert result["evaluation"]["status"] == "PASSED"
    assert len(result["evaluation"]["cases"]) == len(cases) == 2
    assert len(cloud.evaluations) == 2
    assert len({item["request_id"] for item in cloud.invocations}) == 3


@pytest.mark.parametrize("failure", ["quality", "service"])
def test_evaluation_failure_preserves_deployed_runtime(setup, failure):
    journey, cloud = setup
    cloud.score = 0 if failure == "quality" else 1
    cloud.fail_evaluation = failure == "service"
    saved = save(journey, dataset=[{"id": "a", "input": "A question", "expected_response": "Reference answer"}])
    drain(journey)
    result = journey.detail(PERSONAS["alex"], saved["agent_id"])
    assert result["deployment"]["status"] == "DEPLOYED"
    assert result["evaluation"]["status"] == ("FAILED_QUALITY" if failure == "quality" else "ERROR")


def test_double_submit_and_duplicate_worker_do_not_duplicate_cloud_work(setup):
    journey, cloud = setup
    key = uuid4().hex
    first, second = save(journey, key=key), save(journey, key=key)
    assert first == second
    for _ in range(12):
        journey.step(first["job_id"])
    assert len(cloud.created) == 1
    assert len(cloud.invocations) == 1
    repeated = journey.action(PERSONAS["alex"], first["agent_id"], VersionAction(version=1, idempotency_key=uuid4().hex), "deploy")
    assert repeated["job_id"] == first["job_id"]


def test_chat_reuses_server_history_and_isolates_agent_conversations(setup):
    journey, cloud = setup
    first, second = save(journey), save(journey)
    drain(journey)
    request = InvokeAgent(version=1, idempotency_key=uuid4().hex, input="My project is Aurora.")
    started = journey.action(PERSONAS["alex"], first["agent_id"], request, "invoke")
    assert journey.action(PERSONAS["alex"], first["agent_id"], request, "invoke") == started
    drain(journey)
    followup = InvokeAgent(version=1, idempotency_key=uuid4().hex, input="What is its support target?",
                           conversation_id=started["conversation_id"])
    with pytest.raises(HTTPException, match="Conversation not found"):
        journey.action(PERSONAS["alex"], second["agent_id"], followup, "invoke")
    journey.action(PERSONAS["alex"], first["agent_id"], followup, "invoke")
    drain(journey)
    assert cloud.invocations[-1]["history"][0]["text"] == "My project is Aurora."
    assert len(journey.detail(PERSONAS["alex"], first["agent_id"])["conversation"]["messages"]) == 4


def delete_request(journey, agent_id):
    preview = lifecycle.preview(journey, PERSONAS["alex"], agent_id, DeletePreview(version=1), "session-a")
    return DeleteAgent(version=1, idempotency_key=uuid4().hex,
                       confirmation_token=preview["confirmation_token"], confirm_name=preview["name"])


def test_delete_requires_second_confirmation_then_removes_resources_and_private_records(setup):
    journey, cloud = setup
    saved, other = save(journey), save(journey)
    drain(journey)
    request = delete_request(journey, saved["agent_id"])
    assert len(cloud.created) == 2
    for changed, session in (({"confirm_name": "wrong agent"}, "session-a"), ({}, "different-session"),
                             ({"confirmation_token": "x" * 43}, "session-a")):
        with pytest.raises(HTTPException):
            lifecycle.confirm(journey, PERSONAS["alex"], saved["agent_id"], request.model_copy(update=changed), session)
    accepted = lifecycle.confirm(journey, PERSONAS["alex"], saved["agent_id"], request, "session-a")
    assert lifecycle.confirm(journey, PERSONAS["alex"], saved["agent_id"], request, "session-a") == accepted
    with pytest.raises(HTTPException, match="being deleted"):
        journey.action(PERSONAS["alex"], saved["agent_id"], InvokeAgent(
            version=1, idempotency_key=uuid4().hex, input="Do not run"), "invoke")
    drain(journey)
    assert len(cloud.created) == 1
    deleted = journey.detail(PERSONAS["alex"], saved["agent_id"])
    assert deleted["deletion"]["status"] == "DELETED"
    assert "prompt" not in deleted["definition"] and "dataset" not in deleted["definition"]
    assert journey.detail(PERSONAS["alex"], other["agent_id"])["deployment"]["status"] == "DEPLOYED"
    with journey.store.tx() as db:
        assert get(db, "journey-manifest:" + deleted["definition"]["digest"]) is None
        assert db.select("components", where=[("id", "=", "mcp-tavily")]).fetchone()


def test_delete_works_for_failed_deployment_and_revoked_catalog(setup):
    journey, cloud = setup
    cloud.create = lambda *args: (_ for _ in ()).throw(ValueError("Creation rejected"))
    saved = save(journey)
    drain(journey)
    with journey.store.tx() as db:
        db.delete("grants", where=[("persona", "=", "alex")])
    request = delete_request(journey, saved["agent_id"])
    lifecycle.confirm(journey, PERSONAS["alex"], saved["agent_id"], request, "session-a")
    drain(journey)
    assert journey.detail(PERSONAS["alex"], saved["agent_id"])["deletion"]["status"] == "DELETED"


def test_cleanup_failure_is_visible_and_can_be_confirmed_again(setup):
    journey, cloud = setup
    saved = save(journey)
    drain(journey)
    original = cloud.delete_runtime
    cloud.delete_runtime = lambda *args: (_ for _ in ()).throw(ValueError("Cleanup denied"))
    request = delete_request(journey, saved["agent_id"])
    lifecycle.confirm(journey, PERSONAS["alex"], saved["agent_id"], request, "session-a")
    drain(journey)
    assert journey.detail(PERSONAS["alex"], saved["agent_id"])["deletion"]["status"] == "DELETE_FAILED"
    assert len(cloud.created) == 1
    cloud.delete_runtime = original
    request = delete_request(journey, saved["agent_id"])
    lifecycle.confirm(journey, PERSONAS["alex"], saved["agent_id"], request, "session-a")
    drain(journey)
    assert not cloud.created


def test_catalog_new_skill_is_dynamic_and_old_pins_fail_after_revision(setup):
    journey, _ = setup
    publication = SkillPublication(id="new-domain-skill", name="Domain formatting", description="Newly published instruction",
                                   instructions="Always include a section called Open questions.", workspaces=["research"])
    with journey.store.tx() as db:
        publish_skill(db, PERSONAS["admin"], publication)
        db.insert("grants", {"persona": "alex", "component": publication.id})
        put(db, grant_scope(PERSONAS["alex"], publication.id), True)
    options = journey.options(PERSONAS["alex"])
    assert any(item["id"] == publication.id for item in options["choices"]["skills"])
    value = definition(journey)
    value["skills"].append(publication.id)
    value["component_versions"][publication.id] = "1"
    saved = journey.save(PERSONAS["alex"], SaveAgent(definition=AgentDefinition(**value), idempotency_key=uuid4().hex, deploy=False))
    with journey.store.tx() as db:
        manifest = get(db, "journey-manifest:" + journey.owned(db, PERSONAS["alex"], saved["agent_id"])[1]["digest"])
        assert publication.instructions in manifest["skill_instructions"]
        publish_skill(db, PERSONAS["admin"], publication.model_copy(update={"expected_version": "1", "instructions": "Use a different domain format."}))
    with pytest.raises(HTTPException, match="changed version"):
        journey.action(PERSONAS["alex"], saved["agent_id"], VersionAction(version=1, idempotency_key=uuid4().hex), "deploy")
    refreshed = journey.options(PERSONAS["alex"])
    assert next(item for item in refreshed["choices"]["skills"] if item["id"] == publication.id)["version"] == "2"


def test_revoked_tool_and_cross_owner_are_denied(setup):
    journey, _ = setup
    saved = save(journey, deploy=False)
    with pytest.raises(HTTPException) as cross:
        journey.detail(PERSONAS["sam"], saved["agent_id"])
    assert cross.value.status_code == 404
    with journey.store.tx() as db:
        db.delete("grants", where=[("persona", "=", "alex"), ("component", "=", "knowledge-search")])
    with pytest.raises(HTTPException) as denied:
        journey.action(PERSONAS["alex"], saved["agent_id"], VersionAction(version=1, idempotency_key=uuid4().hex), "deploy")
    assert denied.value.status_code == 403


def test_dataset_validation_accepts_absence_but_rejects_duplicates(setup):
    journey, _ = setup
    payload = definition(journey)
    del payload["dataset"]
    assert AgentDefinition(**payload).dataset == []
    assert AgentDefinition(**{**payload, "dataset": None}).dataset == []
    with pytest.raises(ValueError, match="unique"):
        AgentDefinition(**{**payload, "dataset": [{"id": "same", "input": "A"}, {"id": "same", "input": "B"}]})


def test_ai_catalog_and_builder_use_the_same_backend_source(setup):
    from fastapi.testclient import TestClient
    journey, _ = setup
    app = create_app(repository=journey.store, demo_mode=True, worker_enabled=False, journey=journey)
    with TestClient(app, base_url="http://127.0.0.1:5187") as client:
        session = client.post("/api/demo/session", json={"persona_id": "alex"}, headers={"origin": "http://127.0.0.1:5187"})
        assert session.status_code == 200
        snapshot = client.get("/api/catalog").json()
        options = client.get("/api/journey/options").json()
        assert {item["id"] for item in snapshot["items"] if item["kind"] == "skill"} == {
            item["id"] for item in options["choices"]["skills"]}
        assert len(options["templates"]) == 2
        assert client.get("/studio-config.json").json()["journey_enabled"]
        servers = {item["id"] for item in snapshot["items"] if item["kind"] == "mcp_server"}
        assert servers == {"mcp-tavily", "mcp-knowledge"}
        assert all(item["parent_id"] in servers for item in snapshot["items"] if item["kind"] == "tool")


def test_tool_cannot_be_selected_outside_its_mcp_server(setup):
    journey, _ = setup
    value = definition(journey)
    value["tools"].append("web-search")
    value["component_versions"]["web-search"] = "1"
    with pytest.raises(HTTPException, match="belong to a selected MCP server"):
        journey.save(PERSONAS["alex"], SaveAgent(definition=AgentDefinition(**value), idempotency_key=uuid4().hex))


def test_revoking_mcp_server_blocks_its_previously_allowed_tools(setup):
    journey, cloud = setup
    saved = save(journey)
    with journey.store.tx() as db:
        db.delete("grants", where=[("persona", "=", "alex"), ("component", "=", "mcp-knowledge")])
    drain(journey)
    assert not cloud.created
    assert journey.detail(PERSONAS["alex"], saved["agent_id"])["deployment"]["status"] == "FAILED"


def test_foundation_artifact_is_pinned_when_draft_is_saved(setup):
    journey, cloud = setup
    saved = save(journey, deploy=False)
    journey.settings["artifact"] = {"bucket": "another", "key": "changed.zip", "version_id": "2"}
    journey.action(PERSONAS["alex"], saved["agent_id"], VersionAction(version=1, idempotency_key=uuid4().hex), "deploy")
    drain(journey)
    assert next(iter(cloud.created.values()))["artifact"]["version_id"] == "1"


def test_external_mcp_publication_is_scoped_to_approved_workspace():
    from backend.live_catalog import visibility
    item = {"catalog": "journey", "approved": True, "fixture": False, "external": True,
            "discoverable_workspaces": ["research", "operations"], "approved_external_workspaces": ["research"]}
    actor = {**PERSONAS["alex"], "external_allowed": False}
    assert visibility(item, actor)
    assert not visibility(item, {**actor, "workspace": "operations"})
    assert not visibility({**item, "catalog": "other"}, actor)


def test_uncertain_invocation_is_not_automatically_replayed(setup, monkeypatch):
    from botocore.exceptions import ReadTimeoutError
    journey, cloud = setup
    saved = save(journey)
    drain(journey)
    calls = []
    def timeout(*args):
        calls.append(True)
        raise ReadTimeoutError(endpoint_url="https://runtime.example")
    monkeypatch.setattr(cloud, "invoke", timeout)
    job = journey.action(PERSONAS["alex"], saved["agent_id"],
                         InvokeAgent(version=1, idempotency_key=uuid4().hex, input="Question"), "invoke")
    drain(journey)
    journey.step(job["job_id"])
    assert len(calls) == 1
    assert journey.result(PERSONAS["alex"], job["job_id"])["phase"] == "UNKNOWN"
    assert journey.detail(PERSONAS["alex"], saved["agent_id"])["deployment"]["status"] == "DEPLOYED"


def test_expired_evaluate_claim_is_not_replayed(setup):
    journey, cloud = setup
    saved = save(journey, dataset=[{"id": "a", "input": "Question", "expected_response": "Answer"}])
    for _ in range(3):
        journey.step(saved["job_id"])
    evaluation = journey.detail(PERSONAS["alex"], saved["agent_id"])["evaluation"]["job_id"]
    journey.step(evaluation)
    with journey.store.tx() as db:
        state = get(db, "journey-job:" + evaluation)
        assert state["phase"] == "EVAL_SCORE"
        state["claim"] = {"token": "lost-worker", "expires": 0}
        put(db, "journey-job:" + evaluation, state)
    drain(journey)
    assert journey.result(PERSONAS["alex"], evaluation)["phase"] == "UNKNOWN"
    assert not cloud.evaluations


def test_repeated_evaluation_click_reuses_the_active_job(setup):
    journey, _ = setup
    saved = save(journey, dataset=[{"id": "a", "input": "Question"}])
    for _ in range(3):
        journey.step(saved["job_id"])
    evaluation = journey.detail(PERSONAS["alex"], saved["agent_id"])["evaluation"]["job_id"]
    second = journey.action(PERSONAS["alex"], saved["agent_id"],
                            VersionAction(version=1, idempotency_key=uuid4().hex), "evaluation")
    assert second["job_id"] == evaluation
