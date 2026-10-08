"""Separate managed entry points; never log events/cookies/codes/tokens.

Hosted execution uses DynamoDB only; no lifespan or background worker.
"""
import copy
import json
import logging
import os
import time
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

# Job stages that only poll a resource status between steps: journey and
# foundation Runtime readiness (WAIT_RUNTIME) and MCP Python Runtime/log
# provisioning (DEPLOYING). worker_handler drains these in-process instead of
# spending one SQS self-requeue hop per 10-second poll, because Lambda's
# recursive loop detection terminates a Lambda->SQS->same-Lambda chain at
# ~16 invocations (observed live: a ~5 minute VPC Runtime creation needs ~30
# hops and was dropped, visible only as RecursiveInvocationsDropped).
STATUS_WAIT_STAGES = ('WAIT_RUNTIME', 'DEPLOYING')
STATUS_WAIT_RESERVE_MS = 60000


@lru_cache
def store():
    return DynamoStore(os.environ["STATE_TABLE"])


@lru_cache
def application(worker=False):
    jobs = None
    if os.getenv('FOUNDATION_LIVE_ENABLED', '0') == '1':
        from .foundation_jobs import configured_jobs
        jobs = configured_jobs(store(), worker=worker)
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
    allowed = ({("GET", p) for p in ("/auth/login", "/auth/callback", "/studio-config.json", "/auth/verification/status")}
               | {("POST", "/auth/verification/" + p) for p in ("send", "verify")}
               | {("POST", "/api/auth/logout")})
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
    for record in event.get("Records", []):
        job_id = None
        try:
            job_id = json.loads(record["body"])["job_id"]
            app = application(worker=True) if (os.getenv('FOUNDATION_PRODUCER_ENABLED', '0') == '1' or os.getenv('FOUNDATION_LIVE_ENABLED', '0') == '1') else application()
            # Drain bounded cleanup pages in one delivery. Each step still uses
            # Journey's durable claim, deadline and current user authorization.
            for step_index in range(25):
                if context.get_remaining_time_in_millis() < 10000:
                    raise TimeoutError("Durable continuation required")
                with app.state.store.tx() as db:
                    job = db.select("jobs", where=[("id", "=", job_id)]).fetchone()
                if not job or job["stage"] in TERMINAL: break
                if getattr(app.state, "hosted_auth", None) is not None:
                    app.state.hosted_auth.renew_job_session(job_id)
                app.state.step_job(job_id)
                with app.state.store.tx() as db:
                    latest = db.select('jobs', where=[('id', '=', job_id)]).fetchone()
                    from .foundation_runs import get
                    live = get(db, 'foundation-run:' + job_id) or get(db, 'foundation-pending:' + job_id) or get(db, 'journey-job:' + job_id) or get(db, 'mcp-job:' + job_id) or get(db, 'mcp-onboarding-job:' + job_id) or get(db, 'mcp-python-job:' + job_id)
                    python_cleanup = (get(db, 'mcp-python:' + live['server_id'])
                        if live and latest['stage'] == 'DELETING'
                        and job['agent'] == 'mcp-python:' + live.get('server_id', '') else None)
                if live:
                    if latest['stage'] not in TERMINAL:
                        # Drain confirmed progress across frozen MCP resources.
                        # One SQS hop per ZIP part hits Lambda's recursion limit.
                        python_progress = (python_cleanup and python_cleanup.get('deletion')
                            and python_cleanup['phase'] == 'DELETING' and not python_cleanup.get('claim')
                            and python_cleanup['stage'] != json.loads(job['result']).get('stage'))
                        if ((python_progress or (live.get('kind') == 'delete'
                                and live.get('phase') in {'DELETE_DATA', 'DELETE_RECORDS'}
                                and not live.get('claim'))) and step_index < 24
                                and context.get_remaining_time_in_millis() >= 250000):
                            # Reserve room for the SDK's 210-second timeout and
                            # persistence. Paid calls always yield to the queue.
                            continue
                        if (latest['stage'] in STATUS_WAIT_STAGES and step_index < 24
                                and context.get_remaining_time_in_millis() > STATUS_WAIT_RESERVE_MS):
                            # A status poll makes no paid call, so it only needs
                            # time for one more cheap step. Waiting in-process
                            # keeps the whole chain under Lambda's ~16-invocation
                            # recursion cap: each delivery now covers >=240s of
                            # waiting (Worker timeout 300s), every wait stage is
                            # deadline-bounded and goes terminal in-process, so a
                            # chain needs at most ceil(deadline / 250s) hops
                            # (journey deploy: 3600s -> 15 < 16).
                            time.sleep(10)
                            continue
                        boto3.client('sqs').send_message(QueueUrl=os.environ['JOB_QUEUE_URL'],
                            MessageBody=json.dumps({'job_id': job_id}), DelaySeconds=10)
                    break
                if step_index >= len(ACTIVE) - 1:
                    break
        except Exception as exc:
            # A governance read can race another transaction before a job is
            # claimed. Resume the same durable job promptly, preserving its
            # claim/receipt recovery rules instead of waiting the 30m visibility.
            if (job_id and isinstance(exc, HTTPException) and exc.status_code == 409
                    and exc.detail == "Concurrent governance update; reload and retry"):
                try:
                    boto3.client('sqs').send_message(QueueUrl=os.environ['JOB_QUEUE_URL'],
                        MessageBody=json.dumps({'job_id': job_id}), DelaySeconds=10)
                    continue
                except Exception:
                    pass
            # Never log event bodies, tokens or raw SDK exception payloads.
            logging.getLogger(__name__).warning("Worker delivery requires retry: %s", type(exc).__name__)
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


def diagnostic_capture_exchange_handler(event, context):
    """Unregistered and disabled: requires a NEW exact AWS_IAM route/invoke grant.

    Never reuse the product exchange route or accept IAM identity from headers.
    Direct Lambda invocation must be denied to Runtime/browser principals.
    """
    if (os.getenv('DIAGNOSTIC_CAPTURE_EXCHANGE_ENABLED', '0') != '1'
            or event.get('routeKey') != 'POST /internal/diagnostic/capture'):
        return {'statusCode': 403, 'body': '{"code":"LIVE_DISABLED"}'}
    try:
        from botocore.config import Config
        from .diagnostic_capture import require
        from .diagnostic_exchange import exchange
        request = event.get('requestContext', {})
        require(bool(os.getenv('DIAGNOSTIC_CAPTURE_API_ID'))
                and request.get('apiId') == os.environ['DIAGNOSTIC_CAPTURE_API_ID']
                and request.get('stage') == '$default'
                and request.get('http', {}).get('method') == 'POST'
                and event.get('rawPath') == '/internal/diagnostic/capture'
                and event.get('version') == '2.0', 'CAPTURE_EXCHANGE_NAMESPACE_DENIED')
        iam = request.get('authorizer', {}).get('iam', {})
        require(isinstance(iam, dict) and isinstance(iam.get('userArn'), str),
                'VERIFIED_IAM_PRINCIPAL_REQUIRED')
        raw = event.get('body', '')
        require(isinstance(raw, str) and len(raw) <= 8192 and not event.get('isBase64Encoded'),
                'CAPTURE_EXCHANGE_SHAPE_DENIED')
        config = Config(retries={'total_max_attempts': 1}, connect_timeout=3, read_timeout=5)
        control = boto3.client('bedrock-agentcore-control', region_name='us-west-2', config=config)
        repository = DynamoStore(os.environ['STATE_TABLE'],
            resource=boto3.resource('dynamodb', region_name='us-west-2', config=config))
        result = exchange(repository, principal_arn=iam['userArn'], body=json.loads(raw), control=control)
        return {'statusCode': 200, 'headers': {'content-type': 'application/json', 'cache-control': 'no-store'},
                'body': json.dumps(result)}
    except Exception:
        return {'statusCode': 403, 'body': '{"code":"CAPTURE_ADMISSION_DENIED"}'}
