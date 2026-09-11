"""Offline signed-token tests. Not a substitute for a real Cognito browser login."""
import base64
import hashlib
import json
import secrets
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.hosted_auth import FLOW_COOKIE, SESSION_COOKIE, sha
from tests.conftest import create, enqueue, finish

ORIGIN = "https://studio.example.test"

@pytest.fixture
def hosted(tmp_path, monkeypatch):
    monkeypatch.setenv("HOSTED_PREVIEW", "1")
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.setenv("COGNITO_REGION", "us-west-2")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-west-2_TestPool")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "syntheticclient")
    monkeypatch.setenv("COGNITO_DOMAIN", "https://synthetic-studio.auth.us-west-2.amazoncognito.com")
    app = create_app(str(tmp_path / "cloud.sqlite"), worker_enabled=False, public_url=ORIGIN)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(app.state.hosted_auth.keys, "get_signing_key_from_jwt", lambda _: SimpleNamespace(key=key.public_key()))
    with TestClient(app, base_url=ORIGIN) as c:
        yield app, c, key


def token(app, key, subject="subject-a", group="studio-research", **overrides):
    now = int(time.time())
    claims = {"iss": app.state.hosted_auth.issuer, "sub": subject, "iat": now, "exp": now + 1800,
              "client_id": "syntheticclient", "token_use": "access", "scope": "openid email profile", "cognito:groups": [group]}
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "offline-key"})


def authenticate(app, client, key, subject="subject-a", group="studio-research", **overrides):
    access = token(app, key, subject, group, **overrides)
    cookie, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with app.state.store.tx() as db:
        db.execute("INSERT INTO hosted_sessions VALUES (?,?,?,?,?)", (sha(cookie), subject, access, csrf, time.time() + 1800))
    client.cookies.set(SESSION_COOKIE, cookie)
    client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": csrf})
    return cookie


@pytest.mark.parametrize("path", ["/api", "/api/me", "/api/agents", "/api/jobs/unknown", "/api/admin/catalog", "/api/agents/unknown/export", "/api/unknown"])
def test_every_api_requires_real_auth(hosted, path):
    _, c, _ = hosted
    assert c.get(path).status_code == 401
    assert c.post(path, content=b"invalid", headers={"Origin": ORIGIN}).status_code == 401


@pytest.mark.parametrize("path", ["/api/demo/personas", "/api/demo/session"])
def test_demo_routes_closed_even_after_auth(hosted, path):
    app, c, key = hosted
    assert c.get(path).status_code == 401
    authenticate(app, c, key)
    assert c.post(path, json={"persona_id": "admin"}).status_code == 404


def test_public_config_and_pkce(hosted):
    app, c, _ = hosted
    assert c.get("/studio-config.json").json() == {"hosted": True, "mode": "CLOUD-HOSTED DEMO"}
    response = c.get("/auth/login", follow_redirects=False)
    assert response.status_code == 302
    params = parse_qs(urlparse(response.headers["location"]).query)
    assert params["redirect_uri"] == [ORIGIN + "/auth/callback"]
    assert params["code_challenge_method"] == ["S256"]
    assert "Secure" in response.headers["set-cookie"] and "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    with app.state.store.tx() as db:
        flow = db.execute("SELECT * FROM oidc_flows").fetchone()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(flow["verifier"].encode()).digest()).decode().rstrip("=")
    assert params["code_challenge"] == [challenge]
    assert params["state"][0] not in flow["state_hash"]


@pytest.mark.parametrize("overrides", [
    {"iss": "https://wrong.example.test"}, {"client_id": "otherclient"}, {"token_use": "id"},
    {"exp": 1}, {"nbf": int(time.time()) + 3600}, {"scope": "email"}, {"sub": "wrong-subject"},
])
def test_wrong_token_contract_denied(hosted, overrides):
    app, c, key = hosted
    authenticate(app, c, key, **overrides)
    assert c.get("/api/me").status_code == 401


def test_wrong_signature_denied(hosted):
    app, c, _ = hosted
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    authenticate(app, c, other)
    assert c.get("/api/me").status_code == 401


@pytest.mark.parametrize("groups", [[], ["unapproved"], ["studio-admin", "studio-research"], "studio-admin"])
def test_unapproved_or_ambiguous_membership_denied(hosted, groups):
    app, c, key = hosted
    authenticate(app, c, key, **{"cognito:groups": groups})
    assert c.get("/api/me").status_code == 403


def test_business_admin_and_subject_isolation(hosted, payload):
    app, c, key = hosted
    cookie_a = authenticate(app, c, key)
    me = c.get("/api/me").json()
    assert me["persona"]["id"] == "subject-a"
    assert me["persona"]["workspace"] == "research"
    assert me["persona"]["role"] == "business"
    assert c.get("/api/admin/catalog", headers={"X-Role": "admin"}).status_code == 403
    definition = create(c, payload)
    j = enqueue(c, definition)
    assert finish(app, c, j)["stage"] == "PASS"
    assert c.post(f"/api/agents/{definition['agent_id']}/invoke", json={"version": 1, "input": "Aurora launch"}).status_code == 200
    authenticate(app, c, key, "subject-b", "studio-operations")
    assert c.get("/api/me").status_code == 200
    assert c.get("/api/agents").json() == []
    for path in [f"/api/agents/{definition['agent_id']}", f"/api/agents/{definition['agent_id']}/export", f"/api/jobs/{j}"]:
        assert c.get(path).status_code == 404
    authenticate(app, c, key, "subject-admin", "studio-admin")
    assert c.get("/api/admin/catalog").status_code == 200
    assert c.get(f"/api/agents/{definition['agent_id']}").status_code == 404
    assert c.post("/api/admin/grants", json={"persona_id": "subject-a", "component_id": "synthetic-search", "enabled": False}).status_code == 200
    authenticate(app, c, key)
    assert c.get("/api/me").status_code == 200
    # Renewed login must not re-seed a revoked grant.
    assert c.post(f"/api/agents/{definition['agent_id']}/invoke", json={"version": 1, "input": "Aurora launch"}).status_code == 403
    assert cookie_a  # synthetic cookie was never logged


def test_csrf_host_and_cookie_session_logout(hosted):
    app, c, key = hosted
    cookie = authenticate(app, c, key)
    assert c.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert c.post("/api/auth/logout", json={}, headers={"Origin": "https://evil.example.test"}).status_code == 403
    assert c.get("/api/me", headers={"Host": "evil.example.test", "X-Forwarded-Host": "studio.example.test"}).status_code == 400
    assert c.get("/api/me", headers={"X-Forwarded-Host": "evil.example.test", "X-Forwarded-User": "admin"}).json()["persona"]["role"] == "business"
    result = c.post("/api/auth/logout", json={})
    assert result.status_code == 200
    assert urlparse(result.json()["logout_url"]).hostname.endswith(".amazoncognito.com")
    assert "Secure" in result.headers["set-cookie"] and "HttpOnly" in result.headers["set-cookie"]
    c.cookies.set(SESSION_COOKIE, cookie)
    assert c.get("/api/me").status_code == 401


def test_expired_server_session(hosted):
    app, c, key = hosted
    cookie = authenticate(app, c, key)
    with app.state.store.tx() as db:
        db.execute("UPDATE hosted_sessions SET expires=0 WHERE id_hash=?", (sha(cookie),))
    assert c.get("/api/me").status_code == 401


def test_callback_rejects_missing_state_without_token_exchange(hosted):
    _, c, _ = hosted
    assert c.get("/auth/callback?code=synthetic", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 400
    assert c.get("/auth/callback?state=synthetic&code=synthetic", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 400


def test_hosted_and_demo_mutually_exclusive(hosted, tmp_path):
    with pytest.raises(RuntimeError, match="mutually exclusive"):
        create_app(str(tmp_path / "bad.sqlite"), demo_mode=True, public_url=ORIGIN)
