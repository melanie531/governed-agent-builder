"""Signed-token role authorization against both supported repositories."""
import json
import secrets
import time

import pytest

from backend.hosted_auth import SESSION_COOKIE, sha
from tests.conftest import create, enqueue
from tests.test_hosted_auth import hosted, token
from tests.test_serverless import cloud

GROUPS = ["studio-research", "studio-admin", "studio-role-switcher"]


@pytest.fixture(params=["hosted", "cloud"])
def role_app(request):
    return request.getfixturevalue(request.param)


def session(fixture, groups=GROUPS, subject="demo-subject"):
    app, client, key = fixture
    cookie, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    access = token(app, key, subject, **{"cognito:groups": groups})
    with app.state.store.tx() as db:
        db.insert("hosted_sessions", {"id_hash": sha(cookie), "subject": subject,
                  "access_token": access, "csrf": csrf, "expires": time.time() + 1800})
    client.cookies.set(SESSION_COOKIE, cookie)
    client.headers.update({"Origin": "https://studio.example.test", "X-CSRF-Token": csrf})
    return cookie


def test_switch_changes_session_permissions_without_resetting_business_grants(role_app, payload):
    app, client, _ = role_app
    session(role_app)
    initial = client.get("/api/me").json()
    assert initial["persona"]["role"] == "business"
    assert [r["label"] for r in initial["roles"]] == ["Business User", "Platform Admin"]
    assert client.get("/api/admin/catalog").status_code == 403
    agent = create(client, payload)
    job_id = enqueue(client, agent)
    with app.state.store.tx() as db:
        db.delete("grants", where=[("persona", "=", "demo-subject"), ("component", "=", "bedrock-claude")])
        grants = list(db.select("grants", where=[("persona", "=", "demo-subject")]))
    assert client.post("/api/auth/role", json={"group_id": "studio-admin"}).status_code == 200
    assert client.get("/api/me").json()["persona"]["role"] == "admin"
    assert client.get("/api/admin/catalog").status_code == 200
    assert client.get("/api/agents/" + agent["agent_id"]).status_code == 404
    with app.state.store.tx() as db:
        assert json.loads(db.select("principals", where=[("id", "=", "demo-subject")]).fetchone()["body"])["role"] == "business"
    assert client.post("/api/auth/role", json={"group_id": "studio-research"}).status_code == 200
    assert client.get("/api/me").json()["persona"]["workspace"] == "research"
    assert client.get("/api/admin/catalog").status_code == 403
    assert client.get("/api/agents/" + agent["agent_id"]).status_code == 200
    with app.state.store.tx() as db:
        assert db.select("jobs", where=[("id", "=", job_id)]).fetchone()["requester"] == "demo-subject"
    with app.state.store.tx() as db:
        assert [dict(r) for r in db.select("grants", where=[("persona", "=", "demo-subject")])] == [dict(r) for r in grants]
        assert len(list(db.select("audit", where=[("action", "=", "role_switched")]))) == 2


@pytest.mark.parametrize("groups", [["studio-research"], ["studio-research", "studio-role-switcher"]])
def test_user_cannot_select_unassigned_admin_role(role_app, groups):
    _, client, _ = role_app
    session(role_app, groups)
    assert len(client.get("/api/me").json()["roles"]) == 1
    assert client.post("/api/auth/role", json={"group_id": "studio-admin"}).status_code == 403
    assert client.get("/api/admin/catalog").status_code == 403


def test_ambiguous_membership_requires_explicit_switching_enrollment(role_app):
    _, client, _ = role_app
    session(role_app, ["studio-research", "studio-admin"])
    assert client.get("/api/me").status_code == 403
    assert client.post("/api/auth/role", json={"group_id": "studio-admin"}).status_code == 403


def test_switch_is_session_scoped_and_requires_csrf(role_app):
    app, client, _ = role_app
    first = session(role_app)
    first_csrf = client.headers["X-CSRF-Token"]
    assert client.post("/api/auth/role", json={"group_id": "studio-admin"}).status_code == 200
    second = session(role_app)
    assert client.get("/api/me").json()["persona"]["role"] == "business"
    del client.headers["X-CSRF-Token"]
    assert client.post("/api/auth/role", json={"group_id": "studio-admin"}).status_code == 403
    client.cookies.set(SESSION_COOKIE, first)
    client.headers["X-CSRF-Token"] = first_csrf
    assert client.get("/api/me").json()["persona"]["role"] == "admin"
    assert client.post("/api/auth/role", json={"group_id": "studio-admin", "subject": "another-user"}).status_code == 422
    assert client.post("/api/auth/logout").status_code == 200
    client.cookies.set(SESSION_COOKIE, first)
    assert client.post("/api/auth/role", json={"group_id": "studio-admin"}).status_code == 401
    client.cookies.set(SESSION_COOKIE, second)
    assert client.get("/api/me").json()["persona"]["role"] == "business"


def test_old_role_selection_does_not_survive_membership_removal(role_app):
    app, client, key = role_app
    cookie = session(role_app)
    assert client.post("/api/auth/role", json={"group_id": "studio-admin"}).status_code == 200
    # Simulate refreshed signed claims after an operator removes the admin grant.
    access = token(app, key, "demo-subject")
    with app.state.store.tx() as db:
        db.update("hosted_sessions", {"access_token": access}, where=[("id_hash", "=", sha(cookie))])
    assert client.get("/api/admin/catalog").status_code == 403
