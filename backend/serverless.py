"""Separate managed entry points; never log events/cookies/codes/tokens.

Hosted execution uses DynamoDB only; no lifespan or background worker.
"""
import copy
import json
import os
from functools import lru_cache
from http.cookies import SimpleCookie
from types import SimpleNamespace
from urllib.parse import urlparse

import boto3
from fastapi import HTTPException
from mangum import Mangum

from .app import ACTIVE, TERMINAL, create_app
from .dynamo_store import DynamoStore
from .hosted_auth import HostedAuth, PENDING_COOKIE


@lru_cache
def store():
    return DynamoStore(os.environ["STATE_TABLE"])


@lru_cache
def application():
    jobs = None
    if os.getenv('FOUNDATION_LIVE_ENABLED', '0') == '1':
        from .foundation_jobs import configured_jobs
        jobs = configured_jobs(store())
    return create_app(repository=store(), worker_enabled=False, foundation_jobs=jobs)


@lru_cache
def auth():
    return HostedAuth(store(), os.environ["PUBLIC_URL"])


def request_cookies(event):
    headers = {k.lower(): v for k, v in event.get("headers", {}).items()}
    jar = SimpleCookie()
    try:
        jar.load("; ".join(event.get("cookies", [])) or headers.get("cookie", ""))
    except Exception:
        return {}
    return {k: v.value for k, v in jar.items()}


def authorizer(event, context):
    if PENDING_COOKIE in request_cookies(event):
        return {"isAuthorized": True, "context": {"pendingVerification": "deny"}}
    try:
        principal, _ = auth().authenticate(SimpleNamespace(cookies=request_cookies(event)))
        return {"isAuthorized": True, "context": {"subject": principal["id"]}}
    except HTTPException:
        return {"isAuthorized": False}


def proxy(event, context):
    # API Gateway supplies its own Host. Never trust forwarded/browser Host.
    # Fixed configured viewer origin is the only accepted application origin.
    event = copy.deepcopy(event)
    event.setdefault("headers", {})["host"] = urlparse(os.environ["PUBLIC_URL"]).netloc
    return Mangum(application(), lifespan="off")(event, context)


def api_handler(event, context):
    path = event.get("rawPath", "")
    if path != "/api" and not path.startswith("/api/"):
        return {"statusCode": 404, "body": "Not found"}
    authority = event.get("requestContext", {}).get("authorizer", {}).get("lambda", {})
    if authority.get("pendingVerification") == "deny":
        return {"statusCode": 401, "headers": {"cache-control": "no-store"}, "body": "Unauthorized"}
    if not authority.get("subject"):
        return {"statusCode": 401, "body": "Unauthorized"}
    return proxy(event, context)


def auth_handler(event, context):
    path = event.get("rawPath", "")
    method = event.get("requestContext", {}).get("http", {}).get("method")
    allowed = {("GET", p) for p in ("/auth/login", "/auth/callback", "/studio-config.json", "/auth/verification/status")} | {("POST", "/auth/verification/" + p) for p in ("send", "verify")}
    if (method, path) not in allowed:
        return {"statusCode": 404, "body": "Not found"}
    return proxy(event, context)


def dispatch_handler(event, context):
    failures = []
    queue = boto3.client("sqs")
    for record in event.get("Records", []):
        try:
            if record["eventName"] != "INSERT": continue
            image = record["dynamodb"]["NewImage"]
            if image["pk"]["S"] != "jobs": continue
            job = json.loads(image["body"]["S"])
            queue.send_message(QueueUrl=os.environ["JOB_QUEUE_URL"], MessageBody=json.dumps({"job_id": job["id"]}))
        except Exception:
            failures.append({"itemIdentifier": record["dynamodb"]["SequenceNumber"]})
    return {"batchItemFailures": failures}


def worker_handler(event, context):
    failures = []
    app = application()
    for record in event.get("Records", []):
        try:
            job_id = json.loads(record["body"])["job_id"]
            for _ in ACTIVE:
                if context.get_remaining_time_in_millis() < 10000:
                    raise TimeoutError("Durable continuation required")
                with app.state.store.tx() as db:
                    job = db.select("jobs", where=[("id", "=", job_id)]).fetchone()
                if not job or job["stage"] in TERMINAL: break
                app.state.step_job(job_id)
                with app.state.store.tx() as db:
                    latest = db.select('jobs', where=[('id', '=', job_id)]).fetchone()
                    from .foundation_runs import get
                    live = get(db, 'foundation-run:' + job_id) or get(db, 'foundation-pending:' + job_id)
                if live:
                    if latest['stage'] not in TERMINAL:
                        boto3.client('sqs').send_message(QueueUrl=os.environ['JOB_QUEUE_URL'],
                            MessageBody=json.dumps({'job_id': job_id}), DelaySeconds=10)
                    break
        except Exception:
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


def foundation_exchange_handler(event, context):
    """Dedicated AWS_IAM route only. Do not attach to Cognito/browser routes.

    The template isolates this handler behind AWS_IAM and exact API invocation
    permission. Per-Runtime role and deployed policy still need cloud review.
    """
    from foundation_harness.context import Denied
    from .foundation_runs import exchange
    if (os.getenv('FOUNDATION_ADMISSION_ENABLED', '0') != '1'
            or event.get('routeKey') != 'POST /internal/foundation/exchange'):
        return {'statusCode': 403, 'body': '{"code":"LIVE_DISABLED"}'}
    try:
        request = event.get('requestContext', {})
        if (not os.getenv('FOUNDATION_API_ID')
                or request.get('apiId') != os.environ['FOUNDATION_API_ID']
                or request.get('stage') != '$default'
                or request.get('http', {}).get('method') != 'POST'
                or event.get('rawPath') != '/internal/foundation/exchange'
                or event.get('version') != '2.0'):
            raise Denied('EXCHANGE_NAMESPACE_DENIED')
        iam = request.get('authorizer', {}).get('iam', {})
        if not isinstance(iam, dict) or not isinstance(iam.get('userArn'), str):
            raise Denied('VERIFIED_IAM_PRINCIPAL_REQUIRED')
        raw = event.get('body', '')
        if not isinstance(raw, str) or len(raw) > 8192 or event.get('isBase64Encoded'):
            raise Denied('EXCHANGE_SHAPE_DENIED')
        with store().tx() as db:
            result = exchange(db, principal_arn=iam.get('userArn'), body=json.loads(raw))
        return {'statusCode': 200, 'headers': {'content-type': 'application/json'}, 'body': json.dumps(result)}
    except Exception:
        return {'statusCode': 403, 'body': '{"code":"ADMISSION_DENIED"}'}
