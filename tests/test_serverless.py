"""Moto + offline signed JWT integration, not real Cognito authentication."""
import json
import secrets
import time
from types import SimpleNamespace

import boto3
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from moto import mock_aws

from backend.app import create_app
from backend.dynamo_store import DynamoStore, DynamoUnit
from backend.hosted_auth import SESSION_COOKIE, sha
from backend import serverless
from tests.conftest import create, enqueue, finish
from tests.test_hosted_auth import token, ORIGIN


@pytest.fixture
def cloud(monkeypatch):
    for key, value in {"HOSTED_PREVIEW": "1", "AWS_DEFAULT_REGION": "us-west-2", "COGNITO_REGION": "us-west-2", "COGNITO_USER_POOL_ID": "us-west-2_TestPool", "COGNITO_CLIENT_ID": "syntheticclient", "COGNITO_DOMAIN": "https://synthetic-studio.auth.us-west-2.amazoncognito.com", "PUBLIC_URL": ORIGIN, "STATE_TABLE": "synthetic-state", "EXPORT_BUCKET": "synthetic-exports"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("DEMO_MODE", raising=False)
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-west-2")
        resource.create_table(TableName="synthetic-state", KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}], AttributeDefinitions=[{"AttributeName": k, "AttributeType": "S"} for k in ("pk", "sk")], BillingMode="PAY_PER_REQUEST")
        boto3.client("s3").create_bucket(Bucket="synthetic-exports", CreateBucketConfiguration={"LocationConstraint": "us-west-2"})
        store = DynamoStore("synthetic-state", resource)
        store.initialize()
        app = create_app(repository=store, worker_enabled=False, public_url=ORIGIN)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        monkeypatch.setattr(app.state.hosted_auth.keys, "get_signing_key_from_jwt", lambda _: SimpleNamespace(key=key.public_key()))
        monkeypatch.setattr(serverless, "application", lambda: app)
        monkeypatch.setattr(serverless, "auth", lambda: app.state.hosted_auth)
        with TestClient(app, base_url=ORIGIN) as client:
            yield app, client, key


def sign_in(cloud, subject="subject-a", group="studio-research"):
    app, client, key = cloud
    cookie, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    access = token(app, key, subject, group)
    with app.state.store.tx() as db:
        db.insert("hosted_sessions", {"id_hash": sha(cookie), "subject": subject, "access_token": access, "csrf": csrf, "expires": time.time() + 1800})
    client.cookies.set(SESSION_COOKIE, cookie)
    client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": csrf})
    assert client.get("/api/me").status_code == 200
    return cookie


@pytest.mark.parametrize("path", ["/api", "/api/me", "/api/agents", "/api/admin/catalog", "/api/demo/personas", "/api/agents/unknown/export"])
def test_cloud_unauthenticated(cloud, path):
    assert cloud[1].get(path).status_code == 401


def test_dynamo_workflow_and_restart(cloud, payload):
    app, client, _ = cloud
    sign_in(cloud)
    definition = create(client, payload)
    job_id = enqueue(client, definition)
    context = SimpleNamespace(get_remaining_time_in_millis=lambda: 60000)
    event = {"Records": [{"messageId": "synthetic-message", "body": json.dumps({"job_id": job_id})}]}
    assert serverless.worker_handler(event, context) == {"batchItemFailures": []}
    assert serverless.worker_handler(event, context) == {"batchItemFailures": []}
    result = client.get(f"/api/jobs/{job_id}").json()
    assert result["stage"] == "PASS"
    assert len([x for x in result["events"] if x["stage"] == "PASS"]) == 1
    assert client.post(f"/api/agents/{definition['agent_id']}/invoke", json={"version": 1, "input": "Aurora"}).status_code == 200
    assert client.get(f"/api/agents/{definition['agent_id']}/export").status_code == 200
    objects = boto3.client("s3").list_objects_v2(Bucket="synthetic-exports")["Contents"]
    assert objects[0]["Key"].startswith("exports/subject-a/")
    fresh = DynamoStore("synthetic-state")
    with fresh.tx() as db:
        assert db.select("jobs", where=[("id", "=", job_id)]).fetchone()["stage"] == "PASS"
    assert enqueue(client, definition) == job_id
    sign_in(cloud, "subject-b", "studio-operations")
    assert client.get(f"/api/agents/{definition['agent_id']}").status_code == 404
    assert client.get(f"/api/agents/{definition['agent_id']}/export").status_code == 404


def test_atomic_stale_version_fence(cloud):
    store = cloud[0].state.store
    a, b = DynamoUnit(store.table), DynamoUnit(store.table)
    a.update("settings", {"body": "a"}, where=[("key", "=", "policy")])
    b.update("settings", {"body": "b"}, where=[("key", "=", "policy")])
    a.commit()
    with pytest.raises(HTTPException) as error: b.commit()
    assert error.value.status_code == 409
    with store.tx() as db:
        assert db.select("settings", where=[("key", "=", "policy")]).fetchone()["body"] == "a"


def test_read_only_transaction_detects_revocation_race(cloud):
    store = cloud[0].state.store
    a = DynamoUnit(store.table)
    a.select("grants")
    with store.tx() as db: db.insert("grants", {"persona": "synthetic", "component": "synthetic"})
    with pytest.raises(HTTPException): a.commit()


@pytest.mark.parametrize("change", ["logout", "revoke", "version", "policy"])
def test_worker_and_invoke_reauthorization(cloud, payload, change):
    app, client, _ = cloud
    cookie = sign_in(cloud)
    definition = create(client, payload)
    job_id = enqueue(client, definition)
    if change == "logout":
        assert client.post("/api/auth/logout").status_code == 200
    elif change == "version":
        assert client.post(f"/api/agents/{definition['agent_id']}/versions", json={**payload, "base_version": 1}).status_code == 201
    else:
        sign_in(cloud, "subject-admin", "studio-admin")
        if change == "revoke":
            assert client.post("/api/admin/grants", json={"persona_id": "subject-a", "component_id": "synthetic-search", "enabled": False}).status_code == 200
        else:
            assert client.post("/api/admin/policy", json={"require_judge": True, "minimum_score": 1}).status_code == 200
    for _ in range(5): app.state.step_job(job_id)
    with app.state.store.tx() as db:
        assert db.select("jobs", where=[("id", "=", job_id)]).fetchone()["stage"] == "NEEDS_CHANGES"
    if change == "revoke":
        sign_in(cloud)
        with app.state.store.tx() as db:
            assert not db.select("grants", where=[("persona", "=", "subject-a"), ("component", "=", "synthetic-search")]).fetchone()


def test_worker_remaining_time_preserves_retry(cloud, payload):
    sign_in(cloud)
    job_id = enqueue(cloud[1], create(cloud[1], payload))
    event = {"Records": [{"messageId": "retry-me", "body": json.dumps({"job_id": job_id})}]}
    assert serverless.worker_handler(event, SimpleNamespace(get_remaining_time_in_millis=lambda: 1000)) == {"batchItemFailures": [{"itemIdentifier": "retry-me"}]}
    assert serverless.worker_handler(event, SimpleNamespace(get_remaining_time_in_millis=lambda: 60000)) == {"batchItemFailures": []}


def test_authorizer_revocation_and_route_separation(cloud):
    cookie = sign_in(cloud)
    event = {"cookies": [SESSION_COOKIE + "=" + cookie]}
    assert serverless.authorizer(event, None)["isAuthorized"]
    cloud[1].post("/api/auth/logout")
    assert not serverless.authorizer(event, None)["isAuthorized"]
    assert serverless.auth_handler({"rawPath": "/api/agents", "requestContext": {"http": {"method": "GET"}}}, None)["statusCode"] == 404
    assert serverless.api_handler({"rawPath": "/api/me"}, None)["statusCode"] == 401


def test_stream_dispatch_only_job_insert(cloud, monkeypatch):
    queue = boto3.client("sqs").create_queue(QueueName="synthetic-jobs")["QueueUrl"]
    monkeypatch.setenv("JOB_QUEUE_URL", queue)
    record = {"eventName": "INSERT", "dynamodb": {"SequenceNumber": "1", "NewImage": {"pk": {"S": "jobs"}, "body": {"S": json.dumps({"id": "synthetic-job"})}}}}
    assert serverless.dispatch_handler({"Records": [record]}, None) == {"batchItemFailures": []}
    message = boto3.client("sqs").receive_message(QueueUrl=queue)["Messages"][0]
    assert json.loads(message["Body"]) == {"job_id": "synthetic-job"}


def test_pending_gateway_marker_never_grants_business_access(cloud):
    from backend.hosted_auth import PENDING_COOKIE
    cookie = sign_in(cloud)
    event = {"cookies": [SESSION_COOKIE + "=" + cookie, PENDING_COOKIE + "=synthetic"], "rawPath": "/api/me"}
    marker = serverless.authorizer(event, None)
    assert marker == {"isAuthorized": True, "context": {"pendingVerification": "deny"}}
    event["requestContext"] = {"authorizer": {"lambda": marker["context"]}}
    assert serverless.api_handler(event, None)["statusCode"] == 401
    event["requestContext"]["authorizer"]["lambda"]["subject"] = "subject-a"
    assert serverless.api_handler(event, None)["statusCode"] == 401
    cloud[1].cookies.set(PENDING_COOKIE, "synthetic")
    assert cloud[1].post('/api/agents', content=b'x'*70000).status_code == 401


def test_dynamo_verification_atomic_budgets_and_consume(cloud):
    from backend.verification_store import VerificationStore
    from concurrent.futures import ThreadPoolExecutor
    resource = boto3.resource("dynamodb", region_name="us-west-2")
    resource.create_table(TableName="synthetic-verification", KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}], AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}], BillingMode="PAY_PER_REQUEST")
    store = VerificationStore(table_name="synthetic-verification")
    store.put({"id": "test", "expires": int(time.time())+600, "attempts": 0, "sends": 0, "next_send": 0})
    def reserve(_):
        try: store.reserve("test", "attempts"); return True
        except HTTPException: return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(20))) == 5
    assert store.reserve("test", "sends")["sends"] == 1
    with pytest.raises(HTTPException): store.reserve("test", "sends")
    store.consume("test")
    with pytest.raises(HTTPException): store.consume("test")
    with pytest.raises(HTTPException): store.get("test")


def test_dynamo_pending_flow_uses_separate_table(cloud,monkeypatch):
    from tests.test_email_verification import setup
    resource = boto3.resource("dynamodb", region_name="us-west-2")
    resource.create_table(TableName="synthetic-verification", KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}], AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}], BillingMode="PAY_PER_REQUEST")
    monkeypatch.setenv("VERIFICATION_TABLE", "synthetic-verification")
    app,c,provider = setup(cloud,monkeypatch)
    assert c.post('/auth/verification/send',json={}).status_code == 200
    assert c.post('/auth/verification/verify',json={'code':'123456'}).status_code == 200
    assert c.get('/api/me').status_code == 200
    assert resource.Table("synthetic-verification").scan()['Items'] == []
