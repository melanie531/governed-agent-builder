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
from .hosted_auth import HostedAuth


@lru_cache
def store():
    return DynamoStore(os.environ["STATE_TABLE"])


@lru_cache
def application():
    return create_app(repository=store(), worker_enabled=False)


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
    if not event.get("requestContext", {}).get("authorizer", {}).get("lambda", {}).get("subject"):
        return {"statusCode": 401, "body": "Unauthorized"}
    return proxy(event, context)


def auth_handler(event, context):
    path = event.get("rawPath", "")
    method = event.get("requestContext", {}).get("http", {}).get("method")
    if method != "GET" or path not in ("/auth/login", "/auth/callback", "/studio-config.json"):
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
        except Exception:
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
