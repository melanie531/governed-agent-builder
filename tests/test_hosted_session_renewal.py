"""Signed-token HTTP contracts with an offline Cognito token endpoint."""
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest

from backend.hosted_auth import SESSION_COOKIE, sha
from backend import serverless
from backend.dynamo_store import DynamoUnit
from tests.test_hosted_auth import ORIGIN, hosted, token
from tests.test_serverless import cloud
from tests.conftest import create, enqueue

HTTP_CLIENT = httpx.Client


@pytest.fixture(params=["hosted", "cloud"])
def session_app(request):
    return request.getfixturevalue(request.param)


def identity(app, key, subject="subject-a", **changes):
    claims = {"iss": app.state.hosted_auth.issuer, "sub": subject,
              "iat": int(time.time()), "exp": int(time.time()) + 900,
              "aud": "syntheticclient", "token_use": "id",
              "email": "member@example.test", "email_verified": True}
    claims.update(changes)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "offline-key"})


def sign_in(fixture, monkeypatch, refresh="offline-renewal", verified=True):
    app, client, key = fixture
    start = client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    with app.state.store.tx() as db:
        flow = db.select("oidc_flows", where=[("state_hash", "=", sha(state))]).fetchone()
    access = token(app, key, exp=int(time.time()) + 900,
                   scope="openid email profile aws.cognito.signin.user.admin")
    tokens = {"access_token": access, "id_token": identity(
        app, key, nonce=flow["nonce"], email_verified=verified)}
    if refresh is not None:
        tokens["refresh_token"] = refresh

    async def exchange(*args, **kwargs):
        return httpx.Response(200, json=tokens)

    monkeypatch.setattr(httpx.AsyncClient, "post", exchange)
    response = client.get("/auth/callback?state=" + state + "&code=offline", follow_redirects=False)
    assert response.status_code == 303
    return response


def session_row(fixture):
    app, client, _ = fixture
    with app.state.store.tx() as db:
        return dict(db.select("hosted_sessions", where=[
            ("id_hash", "=", sha(client.cookies.get(SESSION_COOKIE)))]).fetchone())


def make_due(fixture, **claims):
    app, _, key = fixture
    row = session_row(fixture)
    # Keep the genuine session deadline but use a correctly signed old access JWT.
    old = token(app, key, iat=int(time.time()) - 1000, exp=int(time.time()) - 100, **claims)
    with app.state.store.tx() as db:
        db.update("hosted_sessions", {"access_token": old}, where=[("id_hash", "=", row["id_hash"])])
    return row


def provider(fixture, monkeypatch, *, status=200, changes=None, before_return=None, response=None):
    app, _, key = fixture
    calls = []
    def handle(request):
        assert str(request.url) == app.state.hosted_auth.domain + "/oauth2/token"
        body = parse_qs(request.content.decode())
        assert body == {"grant_type": ["refresh_token"], "client_id": ["syntheticclient"],
                        "refresh_token": ["offline-renewal"]}
        calls.append(1)
        if before_return:
            before_return()
        result = response if response is not None else {
            "access_token": token(app, key, **{"exp": int(time.time()) + 900, **(changes or {})}),
            "id_token": identity(app, key)}
        return httpx.Response(status, json=result)

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: HTTP_CLIENT(
        **kwargs, transport=httpx.MockTransport(handle)))
    return calls


def test_login_keeps_one_day_session_and_tokens_stay_server_side(session_app, monkeypatch):
    response = sign_in(session_app, monkeypatch)
    row = session_row(session_app)
    assert 86390 <= row["expires"] - time.time() <= 86400
    assert row.get("refresh_token") == "offline-renewal"
    cookie = next(c for c in response.headers.get_list("set-cookie") if
                  c.startswith(SESSION_COOKIE + "=") and "Max-Age=0" not in c)
    assert "Max-Age=86" in cookie and "HttpOnly" in cookie and "Secure" in cookie
    assert "SameSite=strict" in cookie
    result = session_app[1].get("/api/me")
    assert result.status_code == 200
    assert "offline-renewal" not in result.text + str(response.headers)
    assert row["access_token"] not in result.text + str(response.headers)


def test_expired_access_renews_without_changing_cookie_csrf_or_deadline(session_app, monkeypatch):
    sign_in(session_app, monkeypatch)
    before = make_due(session_app)
    calls = provider(session_app, monkeypatch)
    response = session_app[1].get("/api/me")
    assert response.status_code == 200
    after = session_row(session_app)
    assert len(calls) == 1
    for field in ("id_hash", "csrf", "subject", "expires", "active_group"):
        assert after.get(field) == before.get(field)
    assert response.json()["csrf"] == before["csrf"]
    claims = session_app[0].state.hosted_auth.verify(after["access_token"], "access")
    assert claims["exp"] > time.time() + 800
    assert session_app[1].get("/api/me").status_code == 200
    assert len(calls) == 1


def test_absolute_deadline_never_renews(session_app, monkeypatch):
    sign_in(session_app, monkeypatch)
    row = make_due(session_app)
    with session_app[0].state.store.tx() as db:
        db.update("hosted_sessions", {"expires": time.time() - 1}, where=[("id_hash", "=", row["id_hash"])])
    calls = provider(session_app, monkeypatch)
    assert session_app[1].get("/api/me").status_code == 401
    assert not calls


def test_logout_during_token_exchange_cannot_resurrect_session(session_app, monkeypatch):
    sign_in(session_app, monkeypatch)
    row = make_due(session_app)

    def logged_out():
        with session_app[0].state.store.tx() as db:
            db.delete("hosted_sessions", where=[("id_hash", "=", row["id_hash"])])

    calls = provider(session_app, monkeypatch, before_return=logged_out)
    assert session_app[1].get("/api/me").status_code == 401
    assert len(calls) == 1
    with session_app[0].state.store.tx() as db:
        assert not db.select("hosted_sessions", where=[("id_hash", "=", row["id_hash"])]).fetchone()


@pytest.mark.parametrize("changes", [
    {"subject": "someone-else"}, {"client_id": "wrong"}, {"iss": "https://wrong.test"},
    {"token_use": "id"}, {"exp": 1}, {"cognito:groups": ["unapproved"]},
])
def test_renewed_claims_cannot_bypass_identity_or_membership(session_app, monkeypatch, changes):
    sign_in(session_app, monkeypatch)
    make_due(session_app)
    provider(session_app, monkeypatch, changes=changes)
    assert session_app[1].get("/api/me").status_code in (401, 403)


def test_revoked_refresh_requires_login_and_never_returns_provider_details(session_app, monkeypatch):
    sign_in(session_app, monkeypatch)
    make_due(session_app)
    calls = provider(session_app, monkeypatch, status=400,
                     response={"error": "invalid_grant", "error_description": "private-provider-detail"})
    result = session_app[1].get("/api/me")
    assert result.status_code == 401
    assert "private-provider-detail" not in result.text
    assert len(calls) == 1
    assert session_app[1].get("/api/me").status_code == 401
    assert len(calls) == 1


def test_temporary_provider_failure_preserves_session_for_later_request(session_app, monkeypatch):
    sign_in(session_app, monkeypatch)
    before = make_due(session_app)
    provider(session_app, monkeypatch, status=503, response={"error": "private-provider-detail"})
    result = session_app[1].get("/api/me")
    assert result.status_code == 503
    assert "private-provider-detail" not in result.text
    assert session_row(session_app)["expires"] == before["expires"]
    calls = provider(session_app, monkeypatch)
    assert session_app[1].get("/api/me").status_code == 200
    assert len(calls) == 1


def test_legacy_login_without_refresh_keeps_original_short_expiry(session_app, monkeypatch):
    sign_in(session_app, monkeypatch, refresh=None)
    row = session_row(session_app)
    assert 0 < row["expires"] - time.time() <= 900
    assert not row.get("refresh_token")
    make_due(session_app)
    calls = provider(session_app, monkeypatch)
    assert session_app[1].get("/api/me").status_code == 401
    assert not calls


@pytest.mark.parametrize("status", [200, 400])
def test_concurrent_completed_renewal_wins_even_if_old_exchange_is_rejected(session_app, monkeypatch, status):
    sign_in(session_app, monkeypatch)
    row = make_due(session_app)
    app, client, key = session_app
    winner = token(app, key, exp=int(time.time()) + 1200)

    def another_request_finished():
        with app.state.store.tx() as db:
            db.update("hosted_sessions", {"access_token": winner},
                      where=[("id_hash", "=", row["id_hash"])])

    calls = provider(session_app, monkeypatch, status=status,
                     response={"error": "invalid_grant"} if status == 400 else None,
                     before_return=another_request_finished)
    assert client.get("/api/me").status_code == 200
    assert len(calls) == 1
    assert session_row(session_app)["access_token"] == winner
    assert session_row(session_app)["expires"] == row["expires"]


def test_absolute_expiry_during_refresh_does_not_reopen_session(session_app, monkeypatch):
    sign_in(session_app, monkeypatch)
    row = make_due(session_app)

    def deadline_reached():
        with session_app[0].state.store.tx() as db:
            db.update("hosted_sessions", {"expires": time.time() - 1},
                      where=[("id_hash", "=", row["id_hash"])])

    calls = provider(session_app, monkeypatch, before_return=deadline_reached)
    assert session_app[1].get("/api/me").status_code == 401
    assert len(calls) == 1
    assert session_row(session_app)["expires"] < time.time()


def test_forged_old_token_does_not_trigger_refresh(session_app, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import rsa
    sign_in(session_app, monkeypatch)
    row = session_row(session_app)
    forged = token(session_app[0], rsa.generate_private_key(public_exponent=65537, key_size=2048),
                   iat=int(time.time()) - 1000, exp=int(time.time()) - 100)
    with session_app[0].state.store.tx() as db:
        db.update("hosted_sessions", {"access_token": forged}, where=[("id_hash", "=", row["id_hash"])])
    calls = provider(session_app, monkeypatch)
    assert session_app[1].get("/api/me").status_code == 401
    assert not calls


@pytest.mark.parametrize("identity_changes", [{"sub": "other"}, {"email_verified": False},
                                             {"aud": "other"}, {"exp": 1}])
def test_renewal_requires_verified_matching_id_token(session_app, monkeypatch, identity_changes):
    sign_in(session_app, monkeypatch)
    make_due(session_app)
    app, client, key = session_app
    provider(session_app, monkeypatch, response={
        "access_token": token(app, key), "id_token": identity(app, key, **identity_changes)})
    assert client.get("/api/me").status_code == 401


def test_storage_conflict_reconciles_without_repeating_provider_exchange(cloud, monkeypatch):
    sign_in(cloud, monkeypatch)
    row = make_due(cloud)
    store = cloud[0].state.store
    original_commit = DynamoUnit.commit
    races = []

    def racing(unit):
        current = unit.loaded.get("hosted_sessions", {}).get(json.dumps([row["id_hash"]], separators=(",", ":")))
        if (not races and current and current["access_token"] != row["access_token"]
                and current != unit.original.get("hosted_sessions", {}).get(
                    json.dumps([row["id_hash"]], separators=(",", ":")))):
            races.append(1)
            with store.tx() as other:
                other.insert("settings", {"key": "concurrent-write", "body": "true"}, upsert=True)
        original_commit(unit)

    monkeypatch.setattr(DynamoUnit, "commit", racing)
    calls = provider(cloud, monkeypatch)
    assert cloud[1].get("/api/me").status_code == 200
    assert len(calls) == len(races) == 1
    assert session_row(cloud)["expires"] == row["expires"]


def test_verified_email_flow_retains_refresh_only_on_server(session_app, monkeypatch):
    from tests.test_email_verification import Cognito, HEADERS
    app, client, _ = session_app
    if hasattr(app.state.store, "table"):
        import boto3
        monkeypatch.setenv("VERIFICATION_TABLE", "synthetic-verification")
        boto3.resource("dynamodb", region_name="us-west-2").create_table(
            TableName="synthetic-verification",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST")
    sign_in(session_app, monkeypatch, verified=False)
    status = client.get("/auth/verification/status", headers=HEADERS)
    assert status.status_code == 200
    assert "offline-renewal" not in status.text
    client.headers.update({**HEADERS, "Origin": ORIGIN, "X-CSRF-Token": status.json()["csrf"]})
    monkeypatch.setattr(app.state.hosted_auth, "cognito", lambda: Cognito())
    result = client.post("/auth/verification/verify", json={"code": "123456"})
    assert result.status_code == 200
    assert session_row(session_app)["refresh_token"] == "offline-renewal"
    assert 86380 < session_row(session_app)["expires"] - time.time() <= 86400


def test_worker_renews_signed_authority_before_existing_job_checks(cloud, monkeypatch, payload):
    sign_in(cloud, monkeypatch)
    app, client, _ = cloud
    me = client.get("/api/me").json()
    client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": me["csrf"]})
    job_id = enqueue(client, create(client, payload))
    make_due(cloud)
    calls = provider(cloud, monkeypatch)
    event = {"Records": [{"messageId": "session-renewal", "body": json.dumps({"job_id": job_id})}]}
    assert serverless.worker_handler(event, SimpleNamespace(
        get_remaining_time_in_millis=lambda: 60000)) == {"batchItemFailures": []}
    assert len(calls) == 1
    with app.state.store.tx() as db:
        assert db.select("jobs", where=[("id", "=", job_id)]).fetchone()["stage"] == "PASS"


@pytest.mark.parametrize("status,expected_stage,retry", [(400, "NEEDS_CHANGES", False), (503, "VALIDATING", True)])
def test_worker_distinguishes_revoked_authority_from_temporary_renewal_failure(cloud, monkeypatch, payload, status, expected_stage, retry):
    sign_in(cloud, monkeypatch)
    app, client, _ = cloud
    me = client.get("/api/me").json()
    client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": me["csrf"]})
    job_id = enqueue(client, create(client, payload))
    make_due(cloud)
    calls = provider(cloud, monkeypatch, status=status,
                     response={"error": "invalid_grant" if status == 400 else "temporarily_unavailable"})
    event = {"Records": [{"messageId": "session-renewal", "body": json.dumps({"job_id": job_id})}]}
    result = serverless.worker_handler(event, SimpleNamespace(get_remaining_time_in_millis=lambda: 60000))
    assert result == {"batchItemFailures": [{"itemIdentifier": "session-renewal"}] if retry else []}
    assert len(calls) == 1
    with app.state.store.tx() as db:
        assert db.select("jobs", where=[("id", "=", job_id)]).fetchone()["stage"] == expected_stage
