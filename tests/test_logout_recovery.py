"""Logout remains available when business authorization has expired or failed."""
import json
import secrets
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from backend import serverless
from backend.hosted_auth import SESSION_COOKIE, sha
from tests.test_hosted_auth import hosted, token, ORIGIN
from tests.test_serverless import cloud


@pytest.fixture(params=["hosted", "cloud"], ids=["sqlite", "dynamodb"])
def session_backend(request):
    return request.getfixturevalue(request.param)


def insert_session(app, key, *, subject="subject-a", **overrides):
    cookie = secrets.token_urlsafe(32)
    with app.state.store.tx() as db:
        db.insert("hosted_sessions", {
            "id_hash": sha(cookie), "subject": subject,
            "access_token": token(app, key, subject=subject, **overrides),
            "csrf": secrets.token_urlsafe(32), "expires": time.time() + 1800,
        })
    return cookie


@pytest.mark.parametrize("state", [
    "valid", "expired-token", "unapproved-membership", "expired-session",
    "missing-session", "no-cookie",
])
def test_logout_revokes_only_presented_session_even_when_authorization_fails(session_backend, state):
    app, client, key = session_backend
    other = insert_session(app, key, subject="other-subject")
    overrides = {"exp": 1} if state == "expired-token" else (
        {"cognito:groups": []} if state == "unapproved-membership" else {})
    cookie = insert_session(app, key, **overrides)
    if state in {"expired-session", "missing-session", "no-cookie"}:
        with app.state.store.tx() as db:
            if state == "expired-session":
                db.update("hosted_sessions", {"expires": 0}, where=[("id_hash", "=", sha(cookie))])
            else:
                db.delete("hosted_sessions", where=[("id_hash", "=", sha(cookie))])
    if state != "no-cookie":
        client.cookies.set(SESSION_COOKIE, cookie)
    client.headers.update({"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"})
    expected = 200 if state == "valid" else 403 if state == "unapproved-membership" else 401
    assert client.get("/api/me").status_code == expected
    # Session CSRF/token verification cannot be a prerequisite for ending it.
    result = client.post("/api/auth/logout", json={"logout_url": "https://evil.example.test"})
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert all(x in result.headers["set-cookie"] for x in (
        "Max-Age=0", "Path=/", "Secure", "HttpOnly", "SameSite=strict"))
    url = urlparse(result.json()["logout_url"])
    assert url.netloc == urlparse(app.state.hosted_auth.domain).netloc
    assert parse_qs(url.query)["logout_uri"] == [ORIGIN + "/"]
    with app.state.store.tx() as db:
        assert not db.select("hosted_sessions", where=[("id_hash", "=", sha(cookie))]).fetchone()
        assert db.select("hosted_sessions", where=[("id_hash", "=", sha(other))]).fetchone()
    client.cookies.set(SESSION_COOKIE, cookie)
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/auth/role", json={"group_id": "studio-admin"}).status_code == 401
    assert client.post("/api/auth/logout", json={}).status_code == 200


@pytest.mark.parametrize("headers", [
    {}, {"Origin": "null"}, {"Origin": "https://evil.example.test"},
    {"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"},
    {"Origin": ORIGIN, "Sec-Fetch-Site": "same-site"},
])
def test_logout_rejects_non_same_origin_without_revoking_session(session_backend, headers):
    app, client, key = session_backend
    cookie = insert_session(app, key)
    client.cookies.set(SESSION_COOKIE, cookie)
    assert client.post("/api/auth/logout", json={}, headers=headers).status_code == 403
    assert client.get("/api/me").status_code == 200


def test_http_api_logout_works_without_authorizer_context(cloud):
    app, _, key = cloud
    cookie = insert_session(app, key, exp=1)
    assert not serverless.authorizer({"cookies": [SESSION_COOKIE + "=" + cookie]}, None)["isAuthorized"]
    event = {
        "version": "2.0", "routeKey": "POST /api/auth/logout",
        "rawPath": "/api/auth/logout", "rawQueryString": "",
        "headers": {"host": "synthetic.execute-api.example.test",
                    "origin": ORIGIN, "sec-fetch-site": "same-origin"},
        "requestContext": {"http": {"method": "POST", "path": "/api/auth/logout", "sourceIp": "127.0.0.1"}},
        "cookies": [SESSION_COOKIE + "=" + cookie], "body": "{}", "isBase64Encoded": False,
    }
    result = serverless.auth_handler(event, SimpleNamespace())
    assert result["statusCode"] == 200
    assert "logout_url" in json.loads(result["body"])
    assert any(SESSION_COOKIE in x and "Max-Age=0" in x for x in result["cookies"])
    for method, path in [("GET", "/api/auth/logout"), ("POST", "/api/auth/role"), ("GET", "/api/me")]:
        event["rawPath"] = path
        event["requestContext"]["http"]["method"] = method
        assert serverless.auth_handler(event, None)["statusCode"] == 404


@pytest.mark.parametrize("status,detail,failures,expected_calls", [
    (409, "Concurrent governance update; reload and retry", 1, 2),
    (409, "Concurrent governance update; reload and retry", 6, 6),
    (409, "Unrelated conflict", 1, 1),
    (503, "Storage unavailable", 1, 1),
])
def test_logout_retries_only_transaction_conflicts_and_does_not_claim_failed_revocation(
        hosted, monkeypatch, status, detail, failures, expected_calls):
    app, client, key = hosted
    cookie = insert_session(app, key)
    client.cookies.set(SESSION_COOKIE, cookie)
    original = app.state.store.tx
    calls = []

    def interrupted():
        calls.append(1)
        if len(calls) <= failures:
            raise HTTPException(status, detail)
        return original()

    monkeypatch.setattr(app.state.store, "tx", interrupted)
    response = client.post("/api/auth/logout", json={}, headers={"Origin": ORIGIN})
    assert len(calls) == expected_calls
    success = expected_calls > failures
    assert response.status_code == (200 if success else status)
    assert ("Max-Age=0" in response.headers.get("set-cookie", "")) == success
    with original() as db:
        assert bool(db.select("hosted_sessions", where=[("id_hash", "=", sha(cookie))]).fetchone()) != success
