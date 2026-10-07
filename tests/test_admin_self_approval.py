"""Scoped self-approval: only a server-verified ACTIVE Platform Admin role
may decide its own capability-access request. Offline signed tokens only."""
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from .conftest import login
from .test_hosted_auth import hosted, authenticate

SWITCHER = {"cognito:groups": ["studio-research", "studio-admin", "studio-role-switcher"]}


def decision_audit(client):
    rows = [a for a in client.get("/api/admin/audit").json() if a["action"] == "request_decided"]
    assert rows, "expected a request_decided audit row"
    return rows[0], json.loads(rows[0]["detail"])


def test_active_admin_self_approval_succeeds_with_audit(hosted):
    app, c, key = hosted
    authenticate(app, c, key, "switcher", **SWITCHER)
    assert c.get("/api/me").json()["persona"]["role"] == "business"
    r = c.post("/api/requests", json={"component_id": "restricted-insights", "reason": "Research strategy purpose"})
    assert r.status_code == 201
    request_id = r.json()["id"]
    assert c.post("/api/auth/role", json={"group_id": "studio-admin"}).json()["role"] == "admin"
    assert c.post(f"/api/admin/requests/{request_id}/decision",
                  json={"approve": True, "reason": "Audited self-approval as active admin"}).status_code == 200
    row, detail = decision_audit(c)
    assert row["actor"] == "switcher"
    assert detail["actor"] == "switcher" and detail["requester"] == "switcher"
    assert detail["actor_role"] == "admin" and detail["self_approved"] is True
    assert detail["decision"] == "approved" and detail["workspace"] == "research"
    assert c.post("/api/auth/role", json={"group_id": "studio-research"}).json()["role"] == "business"
    assert c.get("/api/catalog/restricted-insights").json()["granted"]


def test_admin_membership_with_active_business_role_denied(hosted):
    app, c, key = hosted
    authenticate(app, c, key, "switcher", **SWITCHER)
    r = c.post("/api/requests", json={"component_id": "restricted-insights", "reason": "Research strategy purpose"})
    assert r.status_code == 201
    response = c.post(f"/api/admin/requests/{r.json()['id']}/decision",
                      json={"approve": True, "reason": "Business view self approval"},
                      headers={"X-Role": "admin", "X-Forwarded-User": "admin"})
    assert response.status_code == 403
    assert response.json()["detail"] == "Platform admin required"


def test_forged_token_cannot_claim_admin(hosted):
    app, c, key = hosted
    authenticate(app, c, key, "switcher", **SWITCHER)
    r = c.post("/api/requests", json={"component_id": "restricted-insights", "reason": "Research strategy purpose"})
    assert r.status_code == 201
    forged = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    authenticate(app, c, forged, "switcher", group="studio-admin")
    assert c.post(f"/api/admin/requests/{r.json()['id']}/decision",
                  json={"approve": True, "reason": "Forged admin signature"}).status_code == 401


def self_request(app, **overrides):
    request_id = "self-" + overrides.get("component", "restricted-insights")
    row = {"id": request_id, "requester": "admin", "workspace": "platform",
           "component": "restricted-insights", "reason": "Platform admin own request",
           "status": "PENDING", "decision": None, "created": time.time(), **overrides}
    with app.state.store.tx() as db:
        db.insert("requests", row)
    return row["id"]


def test_self_approve_demo_mode_succeeds_and_audits(client, app):
    request_id = self_request(app)
    login(client, "admin")
    assert client.post(f"/api/admin/requests/{request_id}/decision",
                       json={"approve": True, "reason": "Active admin self approval"}).status_code == 200
    row, detail = decision_audit(client)
    assert detail["actor"] == "admin" and detail["requester"] == "admin"
    assert detail["actor_role"] == "admin" and detail["self_approved"] is True
    with app.state.store.tx() as db:
        assert db.select("grants", where=[("persona", "=", "admin"), ("component", "=", "restricted-insights")]).fetchone()


def test_self_approve_rejects_short_reason(client, app):
    request_id = self_request(app)
    login(client, "admin")
    assert client.post(f"/api/admin/requests/{request_id}/decision",
                       json={"approve": True, "reason": "ok"}).status_code == 422


def test_self_approve_rejects_changed_workspace(client, app):
    request_id = self_request(app, workspace="research")
    login(client, "admin")
    assert client.post(f"/api/admin/requests/{request_id}/decision",
                       json={"approve": True, "reason": "Workspace drifted since request"}).status_code == 403


def test_self_approve_rejects_version_mismatch(client, app):
    request_id = self_request(app)
    with app.state.store.tx() as db:
        db.insert("settings", {"key": "request-version:" + request_id, "body": json.dumps({"version": "999"})})
    login(client, "admin")
    assert client.post(f"/api/admin/requests/{request_id}/decision",
                       json={"approve": True, "reason": "Stale pinned version"}).status_code == 409


def test_self_approve_rejects_unapproved_component(client, app):
    request_id = self_request(app)
    with app.state.store.tx() as db:
        body = json.loads(db.select("components", columns=["body"], where=[("id", "=", "restricted-insights")]).fetchone()[0])
        body["approved"] = False
        db.update("components", {"body": json.dumps(body)}, where=[("id", "=", "restricted-insights")])
    login(client, "admin")
    # Unapproved capabilities already fail catalog visibility (404) before the
    # explicit 403 approval/data-policy guard; both deny the self-approval.
    assert client.post(f"/api/admin/requests/{request_id}/decision",
                       json={"approve": True, "reason": "Component no longer approved"}).status_code in (403, 404)


def test_self_approve_rejects_data_policy_block(client, app):
    from backend.foundation_runs import get as epoch_get
    request_id = self_request(app)
    with app.state.store.tx() as db:
        body = json.loads(db.select("components", columns=["body"], where=[("id", "=", "restricted-insights")]).fetchone()[0])
        body["external"] = True  # still approved, same version; admin persona disallows external data
        db.update("components", {"body": json.dumps(body)}, where=[("id", "=", "restricted-insights")])
        epoch_before = epoch_get(db, "foundation-epoch") or 0
    login(client, "admin")
    # Data-policy-blocked capabilities already fail catalog visibility (404)
    # before the explicit 403 approval/data-policy guard; both deny.
    assert client.post(f"/api/admin/requests/{request_id}/decision",
                       json={"approve": True, "reason": "Blocked by workspace data policy"}).status_code in (403, 404)
    with app.state.store.tx() as db:
        assert db.select("requests", where=[("id", "=", request_id)]).fetchone()["status"] == "PENDING"
        assert not db.select("grants", where=[("persona", "=", "admin"), ("component", "=", "restricted-insights")]).fetchone()
        assert (epoch_get(db, "foundation-epoch") or 0) == epoch_before
    assert not [a for a in client.get("/api/admin/audit").json() if a["action"] == "request_decided"]


def test_reviewer_path_unchanged_audits_self_approved_false(client):
    login(client)
    r = client.post("/api/requests", json={"component_id": "restricted-insights", "reason": "Research strategy purpose"})
    assert r.status_code == 201
    login(client, "admin")
    assert client.post(f"/api/admin/requests/{r.json()['id']}/decision",
                       json={"approve": True, "reason": "Approved for research"}).status_code == 200
    row, detail = decision_audit(client)
    assert detail["actor"] == "admin" and detail["requester"] == "alex"
    assert detail["actor_role"] == "admin" and detail["self_approved"] is False
