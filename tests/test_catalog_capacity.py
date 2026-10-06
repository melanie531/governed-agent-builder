"""Large catalogs exercise the real DynamoDB repository and durable workers."""
import copy
import json
import time

import pytest
from fastapi import HTTPException

from backend.dynamo_store import DynamoStore, DynamoUnit
from backend.foundation_runs import get, put
from backend.hosted_auth import GROUP_POLICY, HostedAuth
from backend.live_catalog import grant_scope, has_grant
from backend.mcp_management import DeleteConnection, begin
from backend.mcp_onboarding import McpOnboarding, Onboard, Publish
from tests.journey_support import seed
from tests.test_mcp_management import deletion_cloud
from tests.test_mcp_onboarding import BODY, CONFIG, Cloud, drain
from tests.test_serverless import cloud

ADMIN = {"id": "catalog-admin", "role": "admin", "workspace": "platform"}


def catalog(store, count=110, workspace="research"):
    items = [{"id": f"{workspace}-capability-{index}", "catalog": "journey", "kind": "skill",
              "name": f"Capability {index}", "approved": True, "fixture": False, "external": False,
              "discoverable_workspaces": [workspace], "default_grant_workspaces": [workspace]}
             for index in range(count)]
    for offset in range(0, count, 40):
        with store.tx() as db:
            for item in items[offset:offset + 40]:
                db.insert("components", {"id": item["id"], "body": json.dumps(item)})
    return items


@pytest.mark.parametrize("outcome", ["publish", "delete"])
def test_large_publication_prepares_grants_before_atomic_visibility_and_survives_restart(cloud, outcome):
    store = cloud[0].state.store
    settings = seed(store)
    existing = catalog(store, 40)
    people = [{"id": f"research-member-{index}", "role": "business", "workspace": "research"}
              for index in range(8)]
    pending = {"id": "pending-member", "role": "business", "workspace": "research",
               "grant_initialization": "grant"}
    outsider = {"id": "operations-member", "role": "business", "workspace": "operations"}
    with store.tx() as db:
        put(db, "mcp-onboarding", copy.deepcopy(CONFIG))
        for person in [*people, pending, outsider]:
            db.insert("principals", {"id": person["id"], "body": json.dumps(person),
                                     "expires": 0 if person == pending else time.time() + 3600})
    provider = Cloud()
    provider.tools = [{"name": f"tool_{index}", "inputSchema": {"type": "object", "properties": {}}}
                      for index in range(20)]
    deletion_cloud(provider)
    service = McpOnboarding(store, settings, provider)
    created = service.create(ADMIN, Onboard(**BODY))
    drain(service, created["job_id"])
    state = service.tx(lambda db: service.load(db, created["id"]))
    approved = service.publish_request(ADMIN, state["id"], Publish(
        discovery_digest=state["discovery_digest"], tools=[t["name"] for t in provider.tools],
        idempotency_key="large-catalog-publication"))
    for _ in range(2):
        service.step(approved["job_id"])
    service.step(approved["job_id"])
    with store.tx() as db:
        state = service.load(db, state["id"])
        ids = state["publication_catalog_ids"]
        assert len(ids) == 21
        assert state["phase"] == "PUBLISHING"
        assert not [r for r in db.select("components") if r["id"] in ids]
        prepared = [r for r in db.select("grants") if r["component"] in ids]
        assert 0 < len(prepared) < len(people) * len(ids)
    # The durable state, not an in-memory cursor, drives a new worker process.
    service = McpOnboarding(DynamoStore(store.table.name), settings, provider)
    if outcome == "delete":
        provider.tools[0]["description"] = "Changed after the partial batch"
        service.step(approved["job_id"])
        state = service.tx(lambda db: service.load(db, state["id"]))
        assert state["phase"] == "NEEDS_RECONCILIATION"
        removal = begin(service, ADMIN, "onboarding", state["id"], DeleteConnection(
            expected_revision=state["revision"], confirm_name=state["name"],
            idempotency_key="delete-partial-publication"), "delete", None)
        drain(service, removal["job_id"])
    else:
        drain(service, approved["job_id"])
    with store.tx() as db:
        state = service.load(db, state["id"])
        assert state["phase"] == ("READY" if outcome == "publish" else "DELETED")
        published = [json.loads(r["body"]) for r in db.select("components") if r["id"] in ids]
        assert len(published) == (21 if outcome == "publish" else 0)
        for person in people:
            for cid in ids:
                granted = db.select("grants", where=[("persona", "=", person["id"]),
                                                     ("component", "=", cid)]).fetchone()
                assert bool(granted) == (outcome == "publish")
                assert bool(get(db, grant_scope(person, cid))) == (outcome == "publish")
        for person in [pending, outsider]:
            assert not list(db.select("grants", where=[("persona", "=", person["id"])]))
        for item in existing:
            assert json.loads(db.select("components", where=[("id", "=", item["id"])]).fetchone()["body"]) == item
        audits = [r for r in db.select("audit") if r["action"] == "mcp_connection_published"]
        assert len(audits) == (1 if outcome == "publish" else 0)
    assert provider.writes.count("submit") == provider.writes.count("approve") == 1


def test_first_login_and_membership_reset_support_large_catalog_without_regranting_revocations(cloud, monkeypatch):
    app = cloud[0]
    store = app.state.store
    research = catalog(store)
    operations = catalog(store, 105, "operations")
    monkeypatch.setenv("JOURNEY_ENABLED", "1")
    claims = {"sub": "new-member", "exp": time.time() + 3600, "cognito:groups": ["studio-research"]}
    person = app.state.hosted_auth.resolve(claims)
    with store.tx() as db:
        assert all(has_grant(db, person, item) for item in research)
        assert not any(has_grant(db, person, item) for item in operations)
        db.delete("grants", where=[("persona", "=", person["id"]), ("component", "=", research[0]["id"])])
    app.state.hosted_auth.resolve(claims)
    with store.tx() as db:
        assert not has_grant(db, person, research[0])
    person = app.state.hosted_auth.resolve({**claims, "cognito:groups": ["studio-operations"]})
    with store.tx() as db:
        assert not any(has_grant(db, person, item) for item in research)
        assert all(has_grant(db, person, item) for item in operations)
        principal = db.select("principals", where=[("id", "=", person["id"])]).fetchone()
        assert principal["expires"] == claims["exp"]
        assert "grant_initialization" not in json.loads(principal["body"])
        assert {r["component"] for r in db.select("grants", where=[("persona", "=", person["id"])])} == {
            *(item["id"] for item in operations), *GROUP_POLICY["studio-operations"]["grants"]}


def test_interrupted_first_login_remains_expired_and_resumes_after_restart(cloud, monkeypatch):
    app = cloud[0]
    store = app.state.store
    items = catalog(store)
    monkeypatch.setenv("JOURNEY_ENABLED", "1")
    claims = {"sub": "interrupted-member", "exp": time.time() + 3600, "cognito:groups": ["studio-research"]}
    commit = DynamoUnit.commit

    def interrupted(unit):
        commit(unit)
        people = unit.loaded.get("principals", {}).values()
        pending = any(json.loads(r["body"]).get("grant_initialization") for r in people)
        if pending and unit.loaded.get("grants"):
            raise HTTPException(503, "Simulated worker interruption after commit")

    monkeypatch.setattr(DynamoUnit, "commit", interrupted)
    with pytest.raises(HTTPException) as failure:
        app.state.hosted_auth.resolve(claims)
    assert failure.value.status_code == 503
    monkeypatch.setattr(DynamoUnit, "commit", commit)
    with store.tx() as db:
        row = db.select("principals", where=[("id", "=", claims["sub"])]).fetchone()
        assert row["expires"] <= time.time()
        assert json.loads(row["body"])["grant_initialization"]
        assert not db.select("principals", where=[("id", "=", claims["sub"]),
                                                 ("expires", ">", time.time())]).fetchone()
        assert 0 < len(list(db.select("grants", where=[("persona", "=", claims["sub"])]))) < len(items)
    restarted = HostedAuth(DynamoStore(store.table.name), app.state.hosted_auth.public_url)
    person = restarted.resolve(claims)
    with store.tx() as db:
        assert all(has_grant(db, person, item) for item in items)
        row = db.select("principals", where=[("id", "=", person["id"])]).fetchone()
        assert row["expires"] == claims["exp"]
        assert "grant_initialization" not in json.loads(row["body"])
